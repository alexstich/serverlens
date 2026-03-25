<?php

declare(strict_types=1);

namespace ServerLens\Mcp;

use ServerLens\Audit\AuditLogger;
use ServerLens\Auth\RateLimiter;
use ServerLens\Module\ModuleInterface;

final class Server
{
    /** @var array<string, array{tool: Tool, module: ModuleInterface}> */
    private array $tools = [];

    public function __construct(
        private readonly ?AuditLogger $audit = null,
        private readonly ?RateLimiter $rateLimiter = null,
    ) {}

    public function registerModule(ModuleInterface $module): void
    {
        foreach ($module->getTools() as $tool) {
            $this->tools[$tool->name] = [
                'tool' => $tool,
                'module' => $module,
            ];
        }
    }

    public function handleMessage(array $message, string $clientIp = '127.0.0.1'): ?array
    {
        $method = $message['method'] ?? null;
        $id = $message['id'] ?? null;
        $params = $message['params'] ?? [];

        if ($id === null) {
            return null;
        }

        if ($this->rateLimiter && !$this->rateLimiter->allow($clientIp)) {
            return $this->jsonRpcError($id, -32000, 'Rate limit exceeded');
        }

        $startTime = microtime(true);

        $response = match ($method) {
            'initialize' => $this->handleInitialize($id, $params),
            'tools/list' => $this->handleToolsList($id),
            'tools/call' => $this->handleToolsCall($id, $params, $clientIp),
            'ping' => $this->jsonRpcResponse($id, []),
            default => $this->jsonRpcError($id, -32601, "Method not found: {$method}"),
        };

        $durationMs = (int) ((microtime(true) - $startTime) * 1000);

        if ($this->audit && $method === 'tools/call') {
            $toolName = $params['name'] ?? 'unknown';
            $isError = isset($response['result']['isError']) && $response['result']['isError'];
            $this->audit->log($clientIp, $toolName, $params['arguments'] ?? [], !$isError, $durationMs);
        }

        return $response;
    }

    private function handleInitialize(int|string $id, array $params): array
    {
        return $this->jsonRpcResponse($id, [
            'protocolVersion' => '2024-11-05',
            'capabilities' => [
                'tools' => new \stdClass(),
            ],
            'serverInfo' => [
                'name' => 'ServerLens',
                'version' => '1.0.0',
            ],
        ]);
    }

    private function handleToolsList(int|string $id): array
    {
        $tools = [];
        foreach ($this->tools as $entry) {
            $tools[] = $entry['tool']->toArray();
        }

        return $this->jsonRpcResponse($id, ['tools' => $tools]);
    }

    private function handleToolsCall(int|string $id, array $params, string $clientIp): array
    {
        $toolName = $params['name'] ?? '';
        $arguments = $params['arguments'] ?? [];

        if (!isset($this->tools[$toolName])) {
            return $this->jsonRpcError($id, -32602, "Unknown tool: {$toolName}");
        }

        $this->rateLimiter?->incrementConcurrent();

        try {
            $module = $this->tools[$toolName]['module'];
            $result = $module->handleToolCall($toolName, $arguments);
            return $this->jsonRpcResponse($id, $result);
        } catch (\Throwable $e) {
            fwrite(STDERR, "[ServerLens] Tool '{$toolName}' exception: {$e->getMessage()}\n");
            fwrite(STDERR, "[ServerLens]   at {$e->getFile()}:{$e->getLine()}\n");
            return $this->jsonRpcResponse($id, [
                'content' => [
                    ['type' => 'text', 'text' => "Internal error"],
                ],
                'isError' => true,
            ]);
        } finally {
            $this->rateLimiter?->decrementConcurrent();
        }
    }

    private function jsonRpcResponse(int|string $id, mixed $result): array
    {
        return [
            'jsonrpc' => '2.0',
            'id' => $id,
            'result' => $result,
        ];
    }

    private function jsonRpcError(int|string $id, int $code, string $message): array
    {
        return [
            'jsonrpc' => '2.0',
            'id' => $id,
            'error' => [
                'code' => $code,
                'message' => $message,
            ],
        ];
    }
}
