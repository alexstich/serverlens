<?php

declare(strict_types=1);

namespace ServerLens\Transport;

use Psr\Http\Message\ServerRequestInterface;
use React\Http\HttpServer;
use React\Http\Message\Response;
use React\Socket\SocketServer;
use React\Stream\ThroughStream;
use ServerLens\Auth\TokenAuth;

final class SseTransport implements TransportInterface
{
    /** @var array<string, ThroughStream> */
    private array $sessions = [];

    /** @var callable(array, string): ?array */
    private $messageHandler;

    public function __construct(
        private readonly string $host,
        private readonly int $port,
        private readonly ?TokenAuth $auth = null,
    ) {}

    public function onMessage(callable $handler): void
    {
        $this->messageHandler = $handler;
    }

    public function start(): void
    {
        $loop = \React\EventLoop\Loop::get();

        $server = new HttpServer(function (ServerRequestInterface $request) {
            return $this->handleRequest($request);
        });

        $socket = new SocketServer("{$this->host}:{$this->port}", [], $loop);
        $server->listen($socket);

        fwrite(STDERR, "[ServerLens] SSE transport listening on {$this->host}:{$this->port}\n");

        $loop->run();
    }

    private function handleRequest(ServerRequestInterface $request): Response
    {
        $path = $request->getUri()->getPath();
        $method = $request->getMethod();

        if ($method === 'OPTIONS') {
            return $this->corsResponse();
        }

        if ($method === 'GET' && $path === '/sse') {
            return $this->handleSseConnect($request);
        }

        if ($method === 'POST' && $path === '/message') {
            return $this->handleMessage($request);
        }

        return new Response(
            404,
            ['Content-Type' => 'application/json'],
            json_encode(['error' => 'Not Found'])
        );
    }

    private function handleSseConnect(ServerRequestInterface $request): Response
    {
        $clientIp = $this->extractClientIp($request);
        if ($this->auth && !$this->auth->verify($request->getHeaderLine('Authorization'), $clientIp)) {
            return new Response(
                401,
                ['Content-Type' => 'application/json'],
                json_encode(['error' => 'Unauthorized'])
            );
        }

        $sessionId = bin2hex(random_bytes(16));
        $stream = new ThroughStream();
        $this->sessions[$sessionId] = $stream;

        $stream->on('close', function () use ($sessionId) {
            unset($this->sessions[$sessionId]);
            fwrite(STDERR, "[ServerLens] Session closed: {$sessionId}\n");
        });

        fwrite(STDERR, "[ServerLens] New SSE session: {$sessionId}\n");

        \React\EventLoop\Loop::get()->futureTick(function () use ($stream, $sessionId) {
            $endpoint = "/message?sessionId={$sessionId}";
            $stream->write("event: endpoint\ndata: {$endpoint}\n\n");
        });

        return new Response(
            200,
            [
                'Content-Type' => 'text/event-stream',
                'Cache-Control' => 'no-cache',
                'Connection' => 'keep-alive',
                'X-Accel-Buffering' => 'no',
                'Access-Control-Allow-Origin' => '*',
            ],
            $stream
        );
    }

    private function handleMessage(ServerRequestInterface $request): Response
    {
        parse_str($request->getUri()->getQuery(), $query);
        $sessionId = $query['sessionId'] ?? null;

        if (!$sessionId || !isset($this->sessions[$sessionId])) {
            return new Response(
                400,
                $this->jsonHeaders(),
                json_encode(['error' => 'Invalid or expired session'])
            );
        }

        $clientIp = $this->extractClientIp($request);
        if ($this->auth && !$this->auth->verify($request->getHeaderLine('Authorization'), $clientIp)) {
            return new Response(401, $this->jsonHeaders(), json_encode(['error' => 'Unauthorized']));
        }

        $body = (string) $request->getBody();
        $message = json_decode($body, true);

        if (!is_array($message)) {
            return new Response(400, $this->jsonHeaders(), json_encode(['error' => 'Invalid JSON']));
        }

        $clientIp = $this->extractClientIp($request);
        $response = ($this->messageHandler)($message, $clientIp);

        if ($response !== null && isset($message['id'])) {
            $stream = $this->sessions[$sessionId];
            $jsonResponse = json_encode($response, JSON_UNESCAPED_UNICODE);
            $stream->write("event: message\ndata: {$jsonResponse}\n\n");
        }

        return new Response(202, $this->jsonHeaders(), '');
    }

    private function extractClientIp(ServerRequestInterface $request): string
    {
        return $request->getServerParams()['REMOTE_ADDR'] ?? '127.0.0.1';
    }

    private function corsResponse(): Response
    {
        return new Response(204, [
            'Access-Control-Allow-Origin' => '*',
            'Access-Control-Allow-Methods' => 'GET, POST, OPTIONS',
            'Access-Control-Allow-Headers' => 'Authorization, Content-Type',
            'Access-Control-Max-Age' => '86400',
        ]);
    }

    private function jsonHeaders(): array
    {
        return [
            'Content-Type' => 'application/json',
            'Access-Control-Allow-Origin' => '*',
        ];
    }
}
