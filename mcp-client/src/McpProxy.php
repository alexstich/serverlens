<?php

declare(strict_types=1);

namespace ServerLensMcp;

final class McpProxy
{
    /** @var array<string, SshConnection> */
    private array $servers = [];

    /** @var array<string, array<array{name: string, description: string, inputSchema: array}>> */
    private array $remoteTools = [];

    /** @var array<string, array> */
    private array $serverConfigs = [];

    public function __construct(Config $config)
    {
        $this->serverConfigs = $config->getServers();

        foreach ($this->serverConfigs as $name => $serverConfig) {
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
            $this->discoverTools($name, $ssh);
        }

        $totalServers = count($this->servers);
        $totalTools = array_sum(array_map('count', $this->remoteTools));
        fwrite(STDERR, "[MCP] Ready: {$totalServers} server(s), {$totalTools} remote tool(s), 2 MCP tools\n");
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
        return $this->jsonRpc($id, [
            'protocolVersion' => '2024-11-05',
            'capabilities' => ['tools' => new \stdClass()],
            'serverInfo' => [
                'name' => 'ServerLens MCP Proxy',
                'version' => '2.0.0',
                'connected_servers' => array_keys($this->servers),
            ],
        ]);
    }

    private function handleToolsList(int|string $id): array
    {
        $tools = [
            [
                'name' => 'serverlens_list',
                'description' => 'List connected servers and their available tools. Call this first to discover what is available.',
                'inputSchema' => [
                    'type' => 'object',
                    'properties' => [
                        'server' => [
                            'type' => 'string',
                            'description' => 'Optional: show tools only for this server',
                        ],
                    ],
                ],
            ],
            [
                'name' => 'serverlens_call',
                'description' => 'Execute a tool on a remote server. Use serverlens_list first to see available servers and tools.',
                'inputSchema' => [
                    'type' => 'object',
                    'properties' => [
                        'server' => [
                            'type' => 'string',
                            'description' => 'Server name (from serverlens_list)',
                        ],
                        'tool' => [
                            'type' => 'string',
                            'description' => 'Tool name (from serverlens_list)',
                        ],
                        'arguments' => [
                            'type' => 'object',
                            'description' => 'Tool arguments (see tool description for details)',
                        ],
                    ],
                    'required' => ['server', 'tool'],
                ],
            ],
        ];

        return $this->jsonRpc($id, ['tools' => $tools]);
    }

    private function handleToolsCall(int|string $id, array $params): array
    {
        $toolName = $params['name'] ?? '';
        $arguments = $params['arguments'] ?? [];

        return match ($toolName) {
            'serverlens_list' => $this->handleList($id, $arguments),
            'serverlens_call' => $this->handleCall($id, $arguments),
            default => $this->jsonRpc($id, [
                'content' => [['type' => 'text', 'text' => "Unknown tool: {$toolName}. Available: serverlens_list, serverlens_call"]],
                'isError' => true,
            ]),
        };
    }

    private function handleList(int|string $id, array $args): array
    {
        $filterServer = $args['server'] ?? null;

        if ($filterServer !== null) {
            return $this->handleListServer($id, $filterServer);
        }

        $result = [];
        foreach ($this->serverConfigs as $name => $_) {
            $connected = isset($this->servers[$name]);
            $toolCount = count($this->remoteTools[$name] ?? []);
            $result[] = [
                'server' => $name,
                'status' => $connected ? 'connected' : 'disconnected',
                'tools_count' => $toolCount,
            ];
        }

        $text = json_encode([
            'hint' => 'Call serverlens_list with {server: "<name>"} to see available tools for a specific server',
            'servers' => $result,
        ], JSON_PRETTY_PRINT | JSON_UNESCAPED_UNICODE);

        return $this->jsonRpc($id, ['content' => [['type' => 'text', 'text' => $text]]]);
    }

    private function handleListServer(int|string $id, string $serverName): array
    {
        if (!isset($this->serverConfigs[$serverName])) {
            $available = implode(', ', array_keys($this->serverConfigs));
            return $this->jsonRpc($id, [
                'content' => [['type' => 'text', 'text' => "Unknown server: {$serverName}. Available: {$available}"]],
                'isError' => true,
            ]);
        }

        $connected = isset($this->servers[$serverName]);
        $tools = [];

        foreach ($this->remoteTools[$serverName] ?? [] as $tool) {
            $entry = [
                'name' => $tool['name'],
                'description' => $tool['description'],
            ];

            $schema = $tool['inputSchema'] ?? [];
            $props = $schema['properties'] ?? [];
            if (!empty($props) && is_array($props)) {
                $params = [];
                $required = $schema['required'] ?? [];
                foreach ($props as $pName => $pDef) {
                    $desc = $pDef['description'] ?? $pDef['type'] ?? '';
                    $req = in_array($pName, $required, true) ? ' (required)' : '';
                    $params[] = "{$pName}{$req}: {$desc}";
                }
                $entry['parameters'] = $params;
            }

            $tools[] = $entry;
        }

        $text = json_encode([
            'server' => $serverName,
            'status' => $connected ? 'connected' : 'disconnected',
            'hint' => "Call serverlens_call with {server: \"{$serverName}\", tool: \"<name>\", arguments: {...}}",
            'tools' => $tools,
        ], JSON_PRETTY_PRINT | JSON_UNESCAPED_UNICODE);

        return $this->jsonRpc($id, ['content' => [['type' => 'text', 'text' => $text]]]);
    }

    private function handleCall(int|string $id, array $args): array
    {
        $serverName = $args['server'] ?? '';
        $toolName = $args['tool'] ?? '';
        $toolArgs = $args['arguments'] ?? [];

        if ($serverName === '' || $toolName === '') {
            return $this->jsonRpc($id, [
                'content' => [['type' => 'text', 'text' => 'Required: "server" and "tool" parameters']],
                'isError' => true,
            ]);
        }

        if (!isset($this->serverConfigs[$serverName])) {
            $available = implode(', ', array_keys($this->serverConfigs));
            return $this->jsonRpc($id, [
                'content' => [['type' => 'text', 'text' => "Unknown server: {$serverName}. Available: {$available}"]],
                'isError' => true,
            ]);
        }

        $knownTools = array_column($this->remoteTools[$serverName] ?? [], 'name');
        if (!in_array($toolName, $knownTools, true)) {
            $available = implode(', ', $knownTools);
            return $this->jsonRpc($id, [
                'content' => [['type' => 'text', 'text' => "Unknown tool '{$toolName}' on server '{$serverName}'. Available: {$available}"]],
                'isError' => true,
            ]);
        }

        $needReconnect = false;

        if (!isset($this->servers[$serverName])) {
            $needReconnect = true;
        } elseif (!$this->servers[$serverName]->isAlive()) {
            fwrite(STDERR, "[MCP] Server '{$serverName}' connection lost\n");
            $this->servers[$serverName]->close();
            unset($this->servers[$serverName]);
            $needReconnect = true;
        }

        if ($needReconnect) {
            if (!$this->reconnectServer($serverName)) {
                return $this->jsonRpc($id, [
                    'content' => [['type' => 'text', 'text' => "Server '{$serverName}' not connected and reconnect failed"]],
                    'isError' => true,
                ]);
            }
        }

        $server = $this->servers[$serverName];
        $response = $server->callTool($id, $toolName, $toolArgs);

        if ($response === null) {
            fwrite(STDERR, "[MCP] No response from '{$serverName}', attempting reconnect...\n");
            $server->close();
            unset($this->servers[$serverName]);

            if ($this->reconnectServer($serverName)) {
                $response = $this->servers[$serverName]->callTool($id, $toolName, $toolArgs);
            }

            if ($response === null) {
                return $this->jsonRpc($id, [
                    'content' => [['type' => 'text', 'text' => "No response from server '{$serverName}' (reconnect attempted)"]],
                    'isError' => true,
                ]);
            }
        }

        return $response;
    }

    private function reconnectServer(string $serverName): bool
    {
        if (!isset($this->serverConfigs[$serverName])) {
            fwrite(STDERR, "[MCP] No config for server '{$serverName}', cannot reconnect\n");
            return false;
        }

        fwrite(STDERR, "[MCP] Reconnecting to '{$serverName}'...\n");

        $ssh = new SshConnection($serverName, $this->serverConfigs[$serverName]);

        if (!$ssh->connect()) {
            fwrite(STDERR, "[MCP] Reconnect FAILED: cannot connect to '{$serverName}'\n");
            return false;
        }

        if (!$ssh->initialize()) {
            fwrite(STDERR, "[MCP] Reconnect FAILED: cannot initialize '{$serverName}'\n");
            $ssh->close();
            return false;
        }

        $this->servers[$serverName] = $ssh;

        if (!isset($this->remoteTools[$serverName])) {
            $this->discoverTools($serverName, $ssh);
        }

        fwrite(STDERR, "[MCP] Reconnected to '{$serverName}'\n");

        return true;
    }

    private function discoverTools(string $serverName, SshConnection $ssh): void
    {
        $tools = $ssh->getTools();
        $this->remoteTools[$serverName] = $tools;
        fwrite(STDERR, "[MCP] Discovered " . count($tools) . " tools on '{$serverName}'\n");
    }

    private function shutdown(): void
    {
        foreach ($this->servers as $server) {
            $server->close();
        }
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
