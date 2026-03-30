<?php

declare(strict_types=1);

namespace ServerLensMcp;

final class SshConnection
{
    /** @var resource|null */
    private $process = null;
    /** @var resource|null */
    private $stdin = null;
    /** @var resource|null */
    private $stdout = null;
    /** @var resource|null */
    private $stderr = null;
    private bool $initialized = false;
    private int $requestId = 100;

    public function __construct(
        private readonly string $name,
        private readonly array $config,
    ) {}

    public function connect(): bool
    {
        $cmd = $this->buildCommand();
        fwrite(STDERR, "[MCP:{$this->name}] SSH command: {$cmd}\n");

        $descriptors = [
            0 => ['pipe', 'r'],
            1 => ['pipe', 'w'],
            2 => ['pipe', 'w'],
        ];

        $this->process = proc_open($cmd, $descriptors, $pipes);

        if (!is_resource($this->process)) {
            fwrite(STDERR, "[MCP:{$this->name}] Failed to start SSH process\n");
            return false;
        }

        $this->stdin = $pipes[0];
        $this->stdout = $pipes[1];
        $this->stderr = $pipes[2];

        stream_set_blocking($this->stderr, false);
        stream_set_timeout($this->stdout, 30);

        return true;
    }

    public function initialize(): bool
    {
        $response = $this->sendRequest('initialize', [
            'protocolVersion' => '2024-11-05',
            'capabilities' => new \stdClass(),
            'clientInfo' => ['name' => 'serverlens-mcp-proxy', 'version' => '1.0.0'],
        ]);

        if ($response === null) {
            fwrite(STDERR, "[MCP:{$this->name}] Initialize failed: no response\n");
            return false;
        }

        if (isset($response['error'])) {
            $err = $response['error']['message'] ?? 'unknown error';
            fwrite(STDERR, "[MCP:{$this->name}] Initialize failed: {$err}\n");
            return false;
        }

        $this->sendNotification('notifications/initialized');
        $this->initialized = true;

        $serverName = $response['result']['serverInfo']['name'] ?? 'unknown';
        $version = $response['result']['serverInfo']['version'] ?? '?';
        fwrite(STDERR, "[MCP:{$this->name}] Initialized: {$serverName} v{$version}\n");

        return true;
    }

    /** @return array<array{name: string, description: string, inputSchema: array}> */
    public function getTools(): array
    {
        $response = $this->sendRequest('tools/list');
        if ($response === null || !isset($response['result']['tools'])) {
            fwrite(STDERR, "[MCP:{$this->name}] Failed to get tools list\n");
            return [];
        }
        return $response['result']['tools'];
    }

    public function callTool(int|string $originalId, string $toolName, array $arguments): ?array
    {
        $response = $this->sendRequest('tools/call', [
            'name' => $toolName,
            'arguments' => $arguments,
        ]);

        if ($response === null) {
            return null;
        }

        $response['id'] = $originalId;
        return $response;
    }

    public function isAlive(): bool
    {
        if ($this->process === null) {
            return false;
        }

        $status = proc_get_status($this->process);
        return $status['running'] ?? false;
    }

    public function close(): void
    {
        if ($this->stdin) {
            @fclose($this->stdin);
        }
        if ($this->stdout) {
            @fclose($this->stdout);
        }
        if ($this->stderr) {
            @fclose($this->stderr);
        }
        if ($this->process) {
            @proc_terminate($this->process);
            @proc_close($this->process);
        }
    }

    private function sendRequest(string $method, array $params = []): ?array
    {
        $id = $this->requestId++;
        $message = [
            'jsonrpc' => '2.0',
            'id' => $id,
            'method' => $method,
            'params' => empty($params) ? new \stdClass() : $params,
        ];

        $json = json_encode($message, JSON_UNESCAPED_UNICODE);
        $written = @fwrite($this->stdin, $json . "\n");
        @fflush($this->stdin);

        if ($written === false) {
            $this->drainStderr();
            return null;
        }

        return $this->readResponse();
    }

    private function sendNotification(string $method, array $params = []): void
    {
        $message = [
            'jsonrpc' => '2.0',
            'method' => $method,
        ];

        if (!empty($params)) {
            $message['params'] = $params;
        }

        $json = json_encode($message, JSON_UNESCAPED_UNICODE);
        @fwrite($this->stdin, $json . "\n");
        @fflush($this->stdin);
    }

    private function readResponse(): ?array
    {
        $line = @fgets($this->stdout);

        if ($line === false || $line === '') {
            $this->drainStderr();
            return null;
        }

        $data = json_decode(trim($line), true);
        if (!is_array($data)) {
            fwrite(STDERR, "[MCP:{$this->name}] Invalid response: {$line}\n");
            return null;
        }

        return $data;
    }

    private function drainStderr(): void
    {
        if ($this->stderr === null) {
            return;
        }

        while (($line = @fgets($this->stderr)) !== false) {
            $line = trim($line);
            if ($line !== '') {
                fwrite(STDERR, "[MCP:{$this->name}:remote] {$line}\n");
            }
        }
    }

    private function buildCommand(): string
    {
        $ssh = $this->config['ssh'];
        $remote = $this->config['remote'] ?? [];

        $host = $ssh['host'];
        $user = $ssh['user'] ?? 'root';
        $port = (int) ($ssh['port'] ?? 22);
        $key = $ssh['key'] ?? null;
        $options = $ssh['options'] ?? [];

        $php = $remote['php'] ?? 'php';
        $slPath = $remote['serverlens_path'] ?? '/opt/serverlens/bin/serverlens';
        $slConfig = $remote['config_path'] ?? '/etc/serverlens/config.yaml';

        $parts = ['ssh'];
        $parts[] = '-o BatchMode=yes';
        $parts[] = '-o StrictHostKeyChecking=accept-new';
        $parts[] = '-o ServerAliveInterval=15';
        $parts[] = '-o ServerAliveCountMax=3';

        foreach ($options as $optKey => $optVal) {
            if (!is_string($optKey) || is_int($optKey)) {
                continue;
            }
            $parts[] = '-o ' . escapeshellarg("{$optKey}={$optVal}");
        }

        $parts[] = '-p ' . (string) $port;

        if ($key) {
            $expandedKey = str_replace('~', getenv('HOME') ?: '', $key);
            $parts[] = '-i ' . escapeshellarg($expandedKey);
        }

        $parts[] = escapeshellarg("{$user}@{$host}");

        $remoteCmd = "{$php} {$slPath} serve --stdio --config {$slConfig}";
        $parts[] = escapeshellarg($remoteCmd);

        return implode(' ', $parts);
    }

    public function getName(): string
    {
        return $this->name;
    }
}
