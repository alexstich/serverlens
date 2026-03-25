<?php

declare(strict_types=1);

namespace Tests;

use PHPUnit\Framework\TestCase;
use ServerLens\Mcp\Server;
use ServerLens\Mcp\Tool;
use ServerLens\Module\ModuleInterface;

final class McpServerTest extends TestCase
{
    private Server $server;

    protected function setUp(): void
    {
        $this->server = new Server();
        $this->server->registerModule($this->createStubModule());
    }

    public function testInitialize(): void
    {
        $response = $this->server->handleMessage([
            'jsonrpc' => '2.0',
            'id' => 1,
            'method' => 'initialize',
            'params' => [
                'protocolVersion' => '2024-11-05',
                'capabilities' => [],
                'clientInfo' => ['name' => 'test', 'version' => '1.0'],
            ],
        ]);

        $this->assertSame('2.0', $response['jsonrpc']);
        $this->assertSame(1, $response['id']);
        $this->assertSame('2024-11-05', $response['result']['protocolVersion']);
        $this->assertSame('ServerLens', $response['result']['serverInfo']['name']);
    }

    public function testToolsList(): void
    {
        $response = $this->server->handleMessage([
            'jsonrpc' => '2.0',
            'id' => 2,
            'method' => 'tools/list',
            'params' => [],
        ]);

        $tools = $response['result']['tools'];
        $this->assertCount(1, $tools);
        $this->assertSame('test_echo', $tools[0]['name']);
    }

    public function testToolsCall(): void
    {
        $response = $this->server->handleMessage([
            'jsonrpc' => '2.0',
            'id' => 3,
            'method' => 'tools/call',
            'params' => [
                'name' => 'test_echo',
                'arguments' => ['text' => 'hello'],
            ],
        ]);

        $this->assertSame('2.0', $response['jsonrpc']);
        $this->assertSame(3, $response['id']);
        $this->assertSame('echo: hello', $response['result']['content'][0]['text']);
    }

    public function testUnknownTool(): void
    {
        $response = $this->server->handleMessage([
            'jsonrpc' => '2.0',
            'id' => 4,
            'method' => 'tools/call',
            'params' => ['name' => 'nonexistent', 'arguments' => []],
        ]);

        $this->assertArrayHasKey('error', $response);
        $this->assertSame(-32602, $response['error']['code']);
    }

    public function testUnknownMethod(): void
    {
        $response = $this->server->handleMessage([
            'jsonrpc' => '2.0',
            'id' => 5,
            'method' => 'unknown/method',
        ]);

        $this->assertArrayHasKey('error', $response);
        $this->assertSame(-32601, $response['error']['code']);
    }

    public function testNotificationReturnsNull(): void
    {
        $response = $this->server->handleMessage([
            'jsonrpc' => '2.0',
            'method' => 'notifications/initialized',
        ]);

        $this->assertNull($response);
    }

    public function testPing(): void
    {
        $response = $this->server->handleMessage([
            'jsonrpc' => '2.0',
            'id' => 6,
            'method' => 'ping',
        ]);

        $this->assertSame(6, $response['id']);
        $this->assertArrayHasKey('result', $response);
    }

    public function testToolExceptionReturnsError(): void
    {
        $module = new class implements ModuleInterface {
            public function getTools(): array
            {
                return [new Tool('fail_tool', 'Fails', ['type' => 'object', 'properties' => new \stdClass()])];
            }

            public function handleToolCall(string $name, array $arguments): array
            {
                throw new \RuntimeException('Test explosion');
            }
        };

        $server = new Server();
        $server->registerModule($module);

        $response = $server->handleMessage([
            'jsonrpc' => '2.0',
            'id' => 7,
            'method' => 'tools/call',
            'params' => ['name' => 'fail_tool', 'arguments' => []],
        ]);

        $this->assertTrue($response['result']['isError']);
        $this->assertSame('Internal error', $response['result']['content'][0]['text']);
    }

    private function createStubModule(): ModuleInterface
    {
        return new class implements ModuleInterface {
            public function getTools(): array
            {
                return [
                    new Tool('test_echo', 'Echoes text', [
                        'type' => 'object',
                        'properties' => ['text' => ['type' => 'string']],
                        'required' => ['text'],
                    ]),
                ];
            }

            public function handleToolCall(string $name, array $arguments): array
            {
                return [
                    'content' => [['type' => 'text', 'text' => 'echo: ' . ($arguments['text'] ?? '')]],
                ];
            }
        };
    }
}
