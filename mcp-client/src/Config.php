<?php

declare(strict_types=1);

namespace ServerLensMcp;

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
            throw new \RuntimeException("Config not found: {$path}");
        }

        $data = Yaml::parseFile($path);
        if (!is_array($data)) {
            throw new \RuntimeException("Invalid config format");
        }

        $config = new self($data);
        $config->validate();

        return $config;
    }

    /** @return array<string, array{ssh: array, remote: array}> */
    public function getServers(): array
    {
        return $this->data['servers'] ?? [];
    }

    private function validate(): void
    {
        $servers = $this->getServers();
        if (empty($servers)) {
            throw new \RuntimeException("No servers configured");
        }

        foreach ($servers as $name => $server) {
            if (empty($server['ssh']['host'])) {
                throw new \RuntimeException("Server '{$name}': ssh.host is required");
            }
            if (empty($server['ssh']['user'])) {
                throw new \RuntimeException("Server '{$name}': ssh.user is required");
            }
        }
    }
}
