<?php

declare(strict_types=1);

namespace ServerLens\Module;

use ServerLens\Config;
use ServerLens\Mcp\Tool;

final class JournalReader implements ModuleInterface
{
    private const TAIL_MAX_LINES = 500;
    private const SEARCH_MAX_MATCHES = 1000;
    private const SEARCH_SCAN_LINES = 50000;
    private const TIME_SPEC_MAX_LENGTH = 64;

    private bool $enabled;
    /** @var string[] */
    private array $allowedUnits;
    /** @var \Closure(string): ?string */
    private \Closure $executor;

    /**
     * @param \Closure(string): ?string|null $executor Command runner override (for tests)
     */
    public function __construct(Config $config, ?\Closure $executor = null)
    {
        $this->enabled = $config->isJournalEnabled();
        $this->allowedUnits = $config->getAllowedJournalUnits();
        $this->executor = $executor ?? function (string $command): ?string {
            $output = @shell_exec($command);
            return is_string($output) ? $output : null;
        };
    }

    public function getTools(): array
    {
        if (!$this->enabled) {
            return [];
        }

        return [
            new Tool('journal_units', 'List systemd units available for journal reading', [
                'type' => 'object',
                'properties' => new \stdClass(),
            ]),
            new Tool('journal_tail', 'Get the last N lines from a systemd unit journal', [
                'type' => 'object',
                'properties' => [
                    'unit' => ['type' => 'string', 'description' => 'Systemd unit name (from journal_units whitelist)'],
                    'lines' => ['type' => 'integer', 'description' => 'Number of lines (max 500)', 'default' => 100],
                ],
                'required' => ['unit'],
            ]),
            new Tool('journal_search', 'Search a systemd unit journal by substring or regex', [
                'type' => 'object',
                'properties' => [
                    'unit' => ['type' => 'string', 'description' => 'Systemd unit name (from journal_units whitelist)'],
                    'query' => ['type' => 'string', 'description' => 'Search query'],
                    'regex' => ['type' => 'boolean', 'description' => 'Use regex', 'default' => false],
                    'since' => ['type' => 'string', 'description' => 'journalctl time spec, e.g. "2026-07-18" or "3 days ago" (optional)'],
                    'until' => ['type' => 'string', 'description' => 'journalctl time spec (optional)'],
                    'lines' => ['type' => 'integer', 'description' => 'Max matching lines (max 1000)', 'default' => 100],
                ],
                'required' => ['unit', 'query'],
            ]),
        ];
    }

    public function handleToolCall(string $name, array $arguments): array
    {
        if (!$this->enabled) {
            return $this->error("Journal module is disabled");
        }

        return match ($name) {
            'journal_units' => $this->units(),
            'journal_tail' => $this->tail($arguments),
            'journal_search' => $this->search($arguments),
            default => $this->error("Unknown tool: {$name}"),
        };
    }

    private function units(): array
    {
        $result = [
            'allowed_units' => array_values($this->allowedUnits),
            'hint' => 'Use these unit names in journal_tail/journal_search',
        ];

        return $this->ok(json_encode($result, JSON_PRETTY_PRINT | JSON_UNESCAPED_UNICODE));
    }

    private function tail(array $args): array
    {
        $unit = (string) ($args['unit'] ?? '');
        $lines = min(max((int) ($args['lines'] ?? 100), 1), self::TAIL_MAX_LINES);

        if (!$this->isUnitAllowed($unit)) {
            return $this->error("Unit not in whitelist: {$unit}");
        }

        $command = $this->buildTailCommand($unit, $lines);
        $output = ($this->executor)($command);
        if ($output === null) {
            return $this->error("Failed to read journal for unit: {$unit}");
        }

        $output = trim($output);
        if ($output === '') {
            return $this->ok("Journal is empty for unit: {$unit}");
        }

        return $this->ok($output);
    }

    private function search(array $args): array
    {
        $unit = (string) ($args['unit'] ?? '');
        $query = (string) ($args['query'] ?? '');
        $useRegex = (bool) ($args['regex'] ?? false);
        $since = isset($args['since']) ? (string) $args['since'] : null;
        $until = isset($args['until']) ? (string) $args['until'] : null;
        $maxMatches = min(max((int) ($args['lines'] ?? 100), 1), self::SEARCH_MAX_MATCHES);

        if (!$this->isUnitAllowed($unit)) {
            return $this->error("Unit not in whitelist: {$unit}");
        }

        if ($query === '') {
            return $this->error("Query must not be empty");
        }

        if ($useRegex && @preg_match('/' . str_replace('/', '\\/', $query) . '/', '') === false) {
            return $this->error("Invalid regex pattern");
        }

        foreach (['since' => $since, 'until' => $until] as $paramName => $timeSpec) {
            if ($timeSpec !== null && !$this->isValidTimeSpec($timeSpec)) {
                return $this->error("Invalid {$paramName} format. Use journalctl time spec, e.g. \"2026-07-18\" or \"3 days ago\"");
            }
        }

        $command = $this->buildSearchCommand($unit, $since, $until);
        $output = ($this->executor)($command);
        if ($output === null) {
            return $this->error("Failed to read journal for unit: {$unit}");
        }

        // Filtering is done here in PHP: user query never reaches the shell.
        $matches = $this->filterLines(explode("\n", $output), $query, $useRegex, $maxMatches);

        if (empty($matches)) {
            return $this->ok("No matches found for query: {$query}");
        }

        return $this->ok(implode("\n", $matches));
    }

    private function isUnitAllowed(string $unit): bool
    {
        return $unit !== '' && in_array($unit, $this->allowedUnits, true);
    }

    private function buildTailCommand(string $unit, int $lines): string
    {
        return 'journalctl -u ' . escapeshellarg($unit)
            . ' -n ' . $lines
            . ' --no-pager -o short-iso 2>/dev/null';
    }

    private function buildSearchCommand(string $unit, ?string $since, ?string $until): string
    {
        $command = 'journalctl -u ' . escapeshellarg($unit)
            . ' -n ' . self::SEARCH_SCAN_LINES
            . ' --no-pager -o short-iso';

        if ($since !== null) {
            $command .= ' --since ' . escapeshellarg($since);
        }
        if ($until !== null) {
            $command .= ' --until ' . escapeshellarg($until);
        }

        return $command . ' 2>/dev/null';
    }

    private function isValidTimeSpec(string $timeSpec): bool
    {
        if ($timeSpec === '' || strlen($timeSpec) > self::TIME_SPEC_MAX_LENGTH) {
            return false;
        }

        // journalctl time specs: "2026-07-18", "2026-07-18 10:00:00", "3 days ago",
        // "yesterday", "now", "-2h", "+5min". Defense in depth on top of escapeshellarg.
        return (bool) preg_match('/^[a-zA-Z0-9 :.+\-]+$/', $timeSpec);
    }

    /**
     * @param string[] $lines
     * @return string[]
     */
    private function filterLines(array $lines, string $query, bool $useRegex, int $maxMatches): array
    {
        $matches = [];
        $startTime = microtime(true);
        $timeout = 5.0;
        $pattern = '/' . str_replace('/', '\\/', $query) . '/';

        foreach ($lines as $line) {
            if (count($matches) >= $maxMatches) {
                break;
            }

            if (microtime(true) - $startTime > $timeout) {
                $matches[] = "[TIMEOUT: search exceeded {$timeout}s limit]";
                break;
            }

            $line = rtrim($line, "\n\r");
            if ($line === '') {
                continue;
            }

            if ($useRegex) {
                if (@preg_match($pattern, $line)) {
                    $matches[] = $line;
                }
            } else {
                if (str_contains($line, $query)) {
                    $matches[] = $line;
                }
            }
        }

        return $matches;
    }

    private function ok(string $text): array
    {
        return ['content' => [['type' => 'text', 'text' => $text]]];
    }

    private function error(string $text): array
    {
        return ['content' => [['type' => 'text', 'text' => $text]], 'isError' => true];
    }
}
