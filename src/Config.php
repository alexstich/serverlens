<?php

declare(strict_types=1);

namespace ServerLens;

use Symfony\Component\Yaml\Yaml;

final class Config
{
    private array $data;

    private function __construct(array $data)
    {
        $this->data = $data;
    }

    public static function load(string $path): self
    {
        if (!file_exists($path)) {
            throw new \RuntimeException("Config file not found: {$path}");
        }

        $envCandidates = [
            dirname($path) . '/env',
            '/etc/serverlens/env',
        ];
        foreach ($envCandidates as $envPath) {
            if (is_readable($envPath)) {
                self::loadEnvFile($envPath);
                break;
            }
        }

        $data = Yaml::parseFile($path);
        if (!is_array($data)) {
            throw new \RuntimeException("Invalid config format");
        }

        $config = new self($data);
        $config->validate();

        return $config;
    }

    private static function loadEnvFile(string $envPath): void
    {
        if (!is_readable($envPath)) {
            return;
        }

        $lines = file($envPath, FILE_IGNORE_NEW_LINES | FILE_SKIP_EMPTY_LINES);
        if ($lines === false) {
            return;
        }

        foreach ($lines as $line) {
            $line = trim($line);
            if ($line === '' || $line[0] === '#') {
                continue;
            }
            if (str_contains($line, '=')) {
                putenv($line);
            }
        }
    }

    public function get(string $key, mixed $default = null): mixed
    {
        $keys = explode('.', $key);
        $value = $this->data;

        foreach ($keys as $k) {
            if (!is_array($value) || !array_key_exists($k, $value)) {
                return $default;
            }
            $value = $value[$k];
        }

        return $value;
    }

    public function getServerHost(): string
    {
        return $this->get('server.host', '127.0.0.1');
    }

    public function getServerPort(): int
    {
        return (int) $this->get('server.port', 9600);
    }

    public function getTransport(): string
    {
        return $this->get('server.transport', 'sse');
    }

    /** @return array<array{hash: string, created: string, expires: string}> */
    public function getTokens(): array
    {
        return $this->get('auth.tokens', []);
    }

    public function getMaxFailedAttempts(): int
    {
        return (int) $this->get('auth.max_failed_attempts', 5);
    }

    public function getLockoutMinutes(): int
    {
        return (int) $this->get('auth.lockout_minutes', 15);
    }

    public function getRequestsPerMinute(): int
    {
        return (int) $this->get('rate_limiting.requests_per_minute', 60);
    }

    public function getMaxConcurrent(): int
    {
        return (int) $this->get('rate_limiting.max_concurrent', 5);
    }

    public function isAuditEnabled(): bool
    {
        return (bool) $this->get('audit.enabled', true);
    }

    public function getAuditPath(): string
    {
        return $this->get('audit.path', '/var/log/serverlens/audit.log');
    }

    public function shouldLogParams(): bool
    {
        return (bool) $this->get('audit.log_params', false);
    }

    /** @return array<array{name: string, path: string, format: string, max_lines: int}> */
    public function getLogSources(): array
    {
        return $this->get('logs.sources', []);
    }

    /** @return array<array{name: string, path: string, type?: string, redact?: array}> */
    public function getConfigSources(): array
    {
        return $this->get('configs.sources', []);
    }

    /** @return array<array{name: string, host: string, port: int, database: string, user: string, password_env: string, tables: array}> */
    public function getDatabaseConnections(): array
    {
        return $this->get('databases.connections', []);
    }

    public function isSystemEnabled(): bool
    {
        return (bool) $this->get('system.enabled', false);
    }

    /** @return string[] */
    public function getAllowedServices(): array
    {
        return $this->get('system.allowed_services', []);
    }

    /** @return string[] */
    public function getAllowedDockerStacks(): array
    {
        return $this->get('system.allowed_docker_stacks', []);
    }

    private function validate(): void
    {
        $host = $this->getServerHost();
        if (!in_array($host, ['127.0.0.1', 'localhost', '::1'], true)) {
            throw new \RuntimeException(
                "Security: server.host must be localhost (127.0.0.1). Got: {$host}"
            );
        }

        $transport = $this->getTransport();
        if (!in_array($transport, ['sse', 'stdio'], true)) {
            throw new \RuntimeException("server.transport must be 'sse' or 'stdio'");
        }

        if ($this->getTransport() === 'sse' && empty($this->getTokens())) {
            throw new \RuntimeException("auth.tokens must not be empty for SSE transport");
        }
    }
}
