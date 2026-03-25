<?php

declare(strict_types=1);

namespace ServerLens\Module;

use ServerLens\Config;
use ServerLens\Mcp\Tool;
use ServerLens\Security\PathGuard;
use ServerLens\Security\Redactor;

final class ConfigReader implements ModuleInterface
{
    /** @var array<string, array{path: string, type: string, redact: array}> */
    private array $sources = [];
    private PathGuard $pathGuard;
    private Redactor $redactor;

    public function __construct(Config $config)
    {
        $this->pathGuard = new PathGuard();
        $this->redactor = new Redactor();

        foreach ($config->getConfigSources() as $source) {
            $this->sources[$source['name']] = [
                'path' => $source['path'],
                'type' => $source['type'] ?? 'file',
                'redact' => $source['redact'] ?? [],
            ];
        }

        $this->pathGuard->registerSources($config->getConfigSources());
    }

    public function getTools(): array
    {
        return [
            new Tool('config_list', 'List available configuration sources', [
                'type' => 'object',
                'properties' => new \stdClass(),
            ]),
            new Tool('config_read', 'Read configuration file content (secrets redacted)', [
                'type' => 'object',
                'properties' => [
                    'source' => ['type' => 'string', 'description' => 'Config source name'],
                ],
                'required' => ['source'],
            ]),
            new Tool('config_search', 'Search within a configuration file', [
                'type' => 'object',
                'properties' => [
                    'source' => ['type' => 'string', 'description' => 'Config source name'],
                    'query' => ['type' => 'string', 'description' => 'Search query'],
                ],
                'required' => ['source', 'query'],
            ]),
        ];
    }

    public function handleToolCall(string $name, array $arguments): array
    {
        return match ($name) {
            'config_list' => $this->listSources(),
            'config_read' => $this->read($arguments),
            'config_search' => $this->search($arguments),
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
                'type' => $source['type'],
                'available' => $exists,
            ];
        }

        return $this->ok(json_encode($list, JSON_PRETTY_PRINT | JSON_UNESCAPED_UNICODE));
    }

    private function read(array $args): array
    {
        $source = $args['source'] ?? '';

        if (!isset($this->sources[$source])) {
            return $this->error("Unknown config source: {$source}");
        }

        $info = $this->sources[$source];
        $path = $info['path'];

        if ($info['type'] === 'directory') {
            return $this->readDirectory($source, $path, $info['redact']);
        }

        if (!file_exists($path) || !is_readable($path)) {
            return $this->error("Config file not available: {$source}");
        }

        $content = file_get_contents($path);
        if ($content === false) {
            return $this->error("Cannot read config file");
        }

        $content = $this->redactor->redact($content, $info['redact']);

        return $this->ok($content);
    }

    private function readDirectory(string $source, string $path, array $redactRules): array
    {
        if (!is_dir($path)) {
            return $this->error("Config directory not available: {$source}");
        }

        $files = scandir($path);
        if ($files === false) {
            return $this->error("Cannot read config directory");
        }

        $result = [];
        foreach ($files as $file) {
            if ($file === '.' || $file === '..') {
                continue;
            }

            $fullPath = rtrim($path, '/') . '/' . $file;
            if (!is_file($fullPath) || !is_readable($fullPath)) {
                continue;
            }

            $content = file_get_contents($fullPath);
            if ($content === false) {
                continue;
            }

            $content = $this->redactor->redact($content, $redactRules);
            $result[] = "=== {$file} ===\n{$content}";
        }

        if (empty($result)) {
            return $this->ok("Directory is empty: {$source}");
        }

        return $this->ok(implode("\n\n", $result));
    }

    private function search(array $args): array
    {
        $source = $args['source'] ?? '';
        $query = $args['query'] ?? '';

        if (empty($query)) {
            return $this->error("Query must not be empty");
        }

        if (!isset($this->sources[$source])) {
            return $this->error("Unknown config source: {$source}");
        }

        $info = $this->sources[$source];
        $path = $info['path'];

        if ($info['type'] === 'directory') {
            return $this->searchDirectory($path, $query, $info['redact']);
        }

        if (!file_exists($path) || !is_readable($path)) {
            return $this->error("Config file not available: {$source}");
        }

        $content = file_get_contents($path);
        if ($content === false) {
            return $this->error("Cannot read config file");
        }

        $content = $this->redactor->redact($content, $info['redact']);
        $lines = explode("\n", $content);
        $matches = [];

        foreach ($lines as $i => $line) {
            if (stripos($line, $query) !== false) {
                $lineNum = $i + 1;
                $matches[] = "{$lineNum}: {$line}";
            }
        }

        if (empty($matches)) {
            return $this->ok("No matches found for: {$query}");
        }

        return $this->ok(implode("\n", $matches));
    }

    private function searchDirectory(string $path, string $query, array $redactRules): array
    {
        $files = scandir($path);
        if ($files === false) {
            return $this->error("Cannot read config directory");
        }

        $results = [];
        foreach ($files as $file) {
            if ($file === '.' || $file === '..') {
                continue;
            }

            $fullPath = rtrim($path, '/') . '/' . $file;
            if (!is_file($fullPath) || !is_readable($fullPath)) {
                continue;
            }

            $content = file_get_contents($fullPath);
            if ($content === false) {
                continue;
            }

            $content = $this->redactor->redact($content, $redactRules);
            $lines = explode("\n", $content);

            foreach ($lines as $i => $line) {
                if (stripos($line, $query) !== false) {
                    $lineNum = $i + 1;
                    $results[] = "{$file}:{$lineNum}: {$line}";
                }
            }
        }

        if (empty($results)) {
            return $this->ok("No matches found for: {$query}");
        }

        return $this->ok(implode("\n", $results));
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
