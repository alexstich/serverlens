<?php

declare(strict_types=1);

namespace ServerLensMcp;

final class McpProxy
{
    /** @var array<string, SshConnection> */
    private array $servers = [];

    /** @var array<string, array{server: string, tool: string}> */
    private array $toolRegistry = [];

    /** @var array<array{name: string, description: string, inputSchema: array}> */
    private array $toolDefinitions = [];

    public function __construct(Config $config)
    {
        $serverConfigs = $config->getServers();
        $singleServer = count($serverConfigs) === 1;

        foreach ($serverConfigs as $name => $serverConfig) {
            fwrite(STDERR, "[MCP] Connecting to server '{$name}'...\n");

            $ssh = new SshConnection($name, $serverConfig);

            if (!$ssh->connect()) {
                fwrite(STDERR, "[MCP] FAILED: cannot connect to '{$name}'\n");
                continue;
            }

            if (!$ssh->initialize()) {
                fwrite(STDERR, "[MCP] FAILED: cannot initialize '{$name}'\n");
                $ssh->close();
                continue;
            }

            $this->servers[$name] = $ssh;
            $this->discoverTools($name, $ssh, $singleServer);
        }

        $totalTools = count($this->toolDefinitions);
        $totalServers = count($this->servers);
        fwrite(STDERR, "[MCP] Ready: {$totalServers} server(s), {$totalTools} tool(s)\n");
    }

    public function run(): void
    {
        $stdin = fopen('php://stdin', 'r');

        while (!feof($stdin)) {
            $line = fgets($stdin);
            if ($line === false || trim($line) === '') {
                continue;
            }

            $message = json_decode(trim($line), true);
            if (!is_array($message)) {
                continue;
            }

            $response = $this->handleMessage($message);

            if ($response !== null) {
                $json = json_encode($response, JSON_UNESCAPED_UNICODE);
                fwrite(STDOUT, $json . "\n");
                fflush(STDOUT);
            }
        }

        fclose($stdin);
        $this->shutdown();
    }

    private function handleMessage(array $msg): ?array
    {
        $method = $msg['method'] ?? null;
        $id = $msg['id'] ?? null;

        if ($id === null) {
            return null;
        }

        return match ($method) {
            'initialize' => $this->handleInitialize($id),
            'tools/list' => $this->handleToolsList($id),
            'tools/call' => $this->handleToolsCall($id, $msg['params'] ?? []),
            'ping' => $this->jsonRpc($id, []),
            default => $this->jsonRpcError($id, -32601, "Method not found: {$method}"),
        };
    }

    private function handleInitialize(int|string $id): array
    {
        $serverNames = array_keys($this->servers);

        return $this->jsonRpc($id, [
            'protocolVersion' => '2024-11-05',
            'capabilities' => ['tools' => new \stdClass()],
            'serverInfo' => [
                'name' => 'ServerLens MCP Proxy',
                'version' => '1.0.0',
                'connected_servers' => $serverNames,
            ],
        ]);
    }

    private function handleToolsList(int|string $id): array
    {
        return $this->jsonRpc($id, ['tools' => $this->toolDefinitions]);
    }

    private function handleToolsCall(int|string $id, array $params): array
    {
        $toolName = $params['name'] ?? '';
        $arguments = $params['arguments'] ?? [];

        if (!isset($this->toolRegistry[$toolName])) {
            return $this->jsonRpc($id, [
                'content' => [['type' => 'text', 'text' => "Unknown tool: {$toolName}"]],
                'isError' => true,
            ]);
        }

        $reg = $this->toolRegistry[$toolName];
        $serverName = $reg['server'];
        $originalTool = $reg['tool'];

        if (!isset($this->servers[$serverName])) {
            return $this->jsonRpc($id, [
                'content' => [['type' => 'text', 'text' => "Server '{$serverName}' not connected"]],
                'isError' => true,
            ]);
        }

        $server = $this->servers[$serverName];

        if (!$server->isAlive()) {
            fwrite(STDERR, "[MCP] Server '{$serverName}' connection lost, reconnecting...\n");
            unset($this->servers[$serverName]);
            return $this->jsonRpc($id, [
                'content' => [['type' => 'text', 'text' => "Server '{$serverName}' connection lost"]],
                'isError' => true,
            ]);
        }

        $response = $server->callTool($id, $originalTool, $arguments);

        if ($response === null) {
            return $this->jsonRpc($id, [
                'content' => [['type' => 'text', 'text' => "No response from server '{$serverName}'"]],
                'isError' => true,
            ]);
        }

        return $response;
    }

    private function discoverTools(string $serverName, SshConnection $ssh, bool $singleServer): void
    {
        $tools = $ssh->getTools();

        foreach ($tools as $tool) {
            $prefixedName = $singleServer
                ? $tool['name']
                : "{$serverName}__{$tool['name']}";

            $description = $singleServer
                ? $tool['description']
                : "[{$serverName}] {$tool['description']}";

            $this->toolRegistry[$prefixedName] = [
                'server' => $serverName,
                'tool' => $tool['name'],
            ];

            $this->toolDefinitions[] = [
                'name' => $prefixedName,
                'description' => $description,
                'inputSchema' => self::fixSchema($tool['inputSchema'] ?? []),
            ];
        }

        fwrite(STDERR, "[MCP] Discovered " . count($tools) . " tools on '{$serverName}'\n");
    }

    private function shutdown(): void
    {
        foreach ($this->servers as $server) {
            $server->close();
        }
    }

    private static function fixSchema(array $schema): array
    {
        $objectKeys = ['properties', 'patternProperties', 'definitions', 'additionalProperties'];
        foreach ($objectKeys as $key) {
            if (array_key_exists($key, $schema) && $schema[$key] === []) {
                $schema[$key] = new \stdClass();
            }
        }
        if (isset($schema['properties']) && is_array($schema['properties'])) {
            foreach ($schema['properties'] as $k => $v) {
                if (is_array($v)) {
                    $schema['properties'][$k] = self::fixSchema($v);
                }
            }
        }
        if (isset($schema['items']) && is_array($schema['items'])) {
            $schema['items'] = self::fixSchema($schema['items']);
        }
        return $schema;
    }

    private function jsonRpc(int|string $id, mixed $result): array
    {
        return ['jsonrpc' => '2.0', 'id' => $id, 'result' => $result];
    }

    private function jsonRpcError(int|string $id, int $code, string $message): array
    {
        return ['jsonrpc' => '2.0', 'id' => $id, 'error' => ['code' => $code, 'message' => $message]];
    }
}
