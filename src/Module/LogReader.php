<?php

declare(strict_types=1);

namespace ServerLens\Module;

use ServerLens\Config;
use ServerLens\Mcp\Tool;
use ServerLens\Security\PathGuard;

final class LogReader implements ModuleInterface
{
    /** @var array<string, array{path: string, format: string, max_lines: int}> */
    private array $sources = [];
    private PathGuard $pathGuard;

    public function __construct(Config $config)
    {
        $this->pathGuard = new PathGuard();

        foreach ($config->getLogSources() as $source) {
            $this->sources[$source['name']] = [
                'path' => $source['path'],
                'format' => $source['format'] ?? 'plain',
                'max_lines' => (int) ($source['max_lines'] ?? 5000),
            ];
        }

        $this->pathGuard->registerSources($config->getLogSources());
    }

    public function getTools(): array
    {
        return [
            new Tool('logs_list', 'List available log sources', [
                'type' => 'object',
                'properties' => new \stdClass(),
            ]),
            new Tool('logs_tail', 'Get the last N lines from a log file', [
                'type' => 'object',
                'properties' => [
                    'source' => ['type' => 'string', 'description' => 'Log source name'],
                    'lines' => ['type' => 'integer', 'description' => 'Number of lines (max 500)', 'default' => 100],
                ],
                'required' => ['source'],
            ]),
            new Tool('logs_search', 'Search log by substring or regex', [
                'type' => 'object',
                'properties' => [
                    'source' => ['type' => 'string', 'description' => 'Log source name'],
                    'query' => ['type' => 'string', 'description' => 'Search query'],
                    'regex' => ['type' => 'boolean', 'description' => 'Use regex', 'default' => false],
                    'lines' => ['type' => 'integer', 'description' => 'Max matching lines (max 1000)', 'default' => 100],
                ],
                'required' => ['source', 'query'],
            ]),
            new Tool('logs_count', 'Get line count and file size', [
                'type' => 'object',
                'properties' => [
                    'source' => ['type' => 'string', 'description' => 'Log source name'],
                ],
                'required' => ['source'],
            ]),
            new Tool('logs_time_range', 'Get log entries within a time range', [
                'type' => 'object',
                'properties' => [
                    'source' => ['type' => 'string', 'description' => 'Log source name'],
                    'from' => ['type' => 'string', 'description' => 'Start time (ISO 8601 or common format)'],
                    'to' => ['type' => 'string', 'description' => 'End time (ISO 8601 or common format)'],
                    'lines' => ['type' => 'integer', 'description' => 'Max lines', 'default' => 200],
                ],
                'required' => ['source', 'from', 'to'],
            ]),
        ];
    }

    public function handleToolCall(string $name, array $arguments): array
    {
        return match ($name) {
            'logs_list' => $this->listSources(),
            'logs_tail' => $this->tail($arguments),
            'logs_search' => $this->search($arguments),
            'logs_count' => $this->count($arguments),
            'logs_time_range' => $this->timeRange($arguments),
            default => $this->error("Unknown tool: {$name}"),
        };
    }

    private function listSources(): array
    {
        $list = [];
        foreach ($this->sources as $name => $source) {
            $exists = file_exists($source['path']);
            $list[] = [
                'name' => $name,
                'format' => $source['format'],
                'max_lines' => $source['max_lines'],
                'available' => $exists,
            ];
        }

        return $this->ok(json_encode($list, JSON_PRETTY_PRINT | JSON_UNESCAPED_UNICODE));
    }

    private function tail(array $args): array
    {
        $source = $args['source'] ?? '';
        $lines = min((int) ($args['lines'] ?? 100), 500);

        $path = $this->resolveSource($source);
        if ($path === null) {
            return $this->error("Unknown or inaccessible log source: {$source}");
        }

        $maxLines = $this->sources[$source]['max_lines'];
        $lines = min($lines, $maxLines);

        $result = $this->readLastLines($path, $lines);

        return $this->ok(implode("\n", $result));
    }

    private function search(array $args): array
    {
        $source = $args['source'] ?? '';
        $query = $args['query'] ?? '';
        $useRegex = (bool) ($args['regex'] ?? false);
        $maxLines = min((int) ($args['lines'] ?? 100), 1000);

        if (empty($query)) {
            return $this->error("Query must not be empty");
        }

        $path = $this->resolveSource($source);
        if ($path === null) {
            return $this->error("Unknown or inaccessible log source: {$source}");
        }

        if ($useRegex && @preg_match("/{$query}/", '') === false) {
            return $this->error("Invalid regex pattern");
        }

        $matches = [];
        $handle = fopen($path, 'r');
        if ($handle === false) {
            return $this->error("Cannot read log file");
        }

        $startTime = microtime(true);
        $timeout = 5.0;

        while (!feof($handle) && count($matches) < $maxLines) {
            if (microtime(true) - $startTime > $timeout) {
                $matches[] = "[TIMEOUT: search exceeded {$timeout}s limit]";
                break;
            }

            $line = fgets($handle);
            if ($line === false) {
                break;
            }

            $line = rtrim($line, "\n\r");

            if ($useRegex) {
                if (@preg_match("/{$query}/", $line)) {
                    $matches[] = $line;
                }
            } else {
                if (str_contains($line, $query)) {
                    $matches[] = $line;
                }
            }
        }

        fclose($handle);

        if (empty($matches)) {
            return $this->ok("No matches found for query: {$query}");
        }

        return $this->ok(implode("\n", $matches));
    }

    private function count(array $args): array
    {
        $source = $args['source'] ?? '';

        $path = $this->resolveSource($source);
        if ($path === null) {
            return $this->error("Unknown or inaccessible log source: {$source}");
        }

        $lineCount = 0;
        $handle = fopen($path, 'r');
        if ($handle !== false) {
            while (!feof($handle)) {
                $chunk = fread($handle, 65536);
                if ($chunk !== false) {
                    $lineCount += substr_count($chunk, "\n");
                }
            }
            fclose($handle);
        }

        $size = filesize($path) ?: 0;
        $sizeHuman = $this->formatBytes($size);

        $info = [
            'source' => $source,
            'lines' => $lineCount,
            'size_bytes' => $size,
            'size_human' => $sizeHuman,
        ];

        return $this->ok(json_encode($info, JSON_PRETTY_PRINT));
    }

    private function timeRange(array $args): array
    {
        $source = $args['source'] ?? '';
        $from = strtotime($args['from'] ?? '');
        $to = strtotime($args['to'] ?? '');
        $maxLines = min((int) ($args['lines'] ?? 200), 1000);

        if ($from === false || $to === false) {
            return $this->error("Invalid date format. Use ISO 8601 or common format.");
        }

        $path = $this->resolveSource($source);
        if ($path === null) {
            return $this->error("Unknown or inaccessible log source: {$source}");
        }

        $matches = [];
        $handle = fopen($path, 'r');
        if ($handle === false) {
            return $this->error("Cannot read log file");
        }

        while (!feof($handle) && count($matches) < $maxLines) {
            $line = fgets($handle);
            if ($line === false) {
                break;
            }

            $ts = $this->extractTimestamp($line);
            if ($ts !== null && $ts >= $from && $ts <= $to) {
                $matches[] = rtrim($line, "\n\r");
            }
        }

        fclose($handle);

        if (empty($matches)) {
            return $this->ok("No entries found in the specified time range");
        }

        return $this->ok(implode("\n", $matches));
    }

    private function resolveSource(string $name): ?string
    {
        if (!isset($this->sources[$name])) {
            return null;
        }

        $path = $this->sources[$name]['path'];
        if (!file_exists($path) || !is_readable($path)) {
            return null;
        }

        $resolved = realpath($path);
        if ($resolved === false) {
            return null;
        }

        return $resolved;
    }

    /** @return string[] */
    private function readLastLines(string $path, int $count): array
    {
        $lines = [];
        $handle = fopen($path, 'r');
        if ($handle === false) {
            return [];
        }

        fseek($handle, 0, SEEK_END);
        $pos = ftell($handle);
        $buffer = '';
        $lineCount = 0;

        while ($pos > 0 && $lineCount < $count) {
            $pos--;
            fseek($handle, $pos);
            $char = fgetc($handle);

            if ($char === "\n") {
                if ($buffer !== '') {
                    array_unshift($lines, $buffer);
                    $lineCount++;
                    $buffer = '';
                }
            } else {
                $buffer = $char . $buffer;
            }
        }

        if ($buffer !== '' && $lineCount < $count) {
            array_unshift($lines, $buffer);
        }

        fclose($handle);

        return array_slice($lines, 0, $count);
    }

    private function extractTimestamp(string $line): ?int
    {
        // nginx combined: [25/Mar/2026:14:30:22 +0300]
        if (preg_match('/\[(\d{2}\/\w{3}\/\d{4}:\d{2}:\d{2}:\d{2}\s[+\-]\d{4})\]/', $line, $m)) {
            $ts = strtotime($m[1]);
            return $ts !== false ? $ts : null;
        }

        // ISO 8601: 2026-03-25T14:30:22
        if (preg_match('/(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2})/', $line, $m)) {
            $ts = strtotime($m[1]);
            return $ts !== false ? $ts : null;
        }

        // syslog: Mar 25 14:30:22
        if (preg_match('/^(\w{3}\s+\d{1,2}\s+\d{2}:\d{2}:\d{2})/', $line, $m)) {
            $ts = strtotime($m[1]);
            return $ts !== false ? $ts : null;
        }

        return null;
    }

    private function formatBytes(int $bytes): string
    {
        $units = ['B', 'KB', 'MB', 'GB'];
        $i = 0;
        $size = (float) $bytes;
        while ($size >= 1024 && $i < count($units) - 1) {
            $size /= 1024;
            $i++;
        }
        return round($size, 2) . ' ' . $units[$i];
    }

    private function ok(string $text): array
    {
        return [
            'content' => [['type' => 'text', 'text' => $text]],
        ];
    }

    private function error(string $text): array
    {
        return [
            'content' => [['type' => 'text', 'text' => $text]],
            'isError' => true,
        ];
    }
}
