<?php

declare(strict_types=1);

namespace ServerLens\Transport;

final class StdioTransport implements TransportInterface
{
    /** @var callable(array, string): ?array */
    private $messageHandler;

    public function onMessage(callable $handler): void
    {
        $this->messageHandler = $handler;
    }

    public function start(): void
    {
        fwrite(STDERR, "[ServerLens] Stdio transport started\n");

        $stdin = fopen('php://stdin', 'r');
        if ($stdin === false) {
            throw new \RuntimeException('Cannot open stdin');
        }

        stream_set_blocking($stdin, true);

        while (!feof($stdin)) {
            $line = fgets($stdin);
            if ($line === false || $line === '') {
                continue;
            }

            $line = trim($line);
            if ($line === '') {
                continue;
            }

            $message = json_decode($line, true);
            if (!is_array($message)) {
                $this->writeError(-32700, 'Parse error', null);
                continue;
            }

            $response = ($this->messageHandler)($message, 'stdio');

            if ($response !== null) {
                $this->write($response);
            }
        }

        fclose($stdin);
    }

    private function write(array $data): void
    {
        $json = json_encode($data, JSON_UNESCAPED_UNICODE);
        fwrite(STDOUT, $json . "\n");
        fflush(STDOUT);
    }

    private function writeError(int $code, string $message, int|string|null $id): void
    {
        $this->write([
            'jsonrpc' => '2.0',
            'id' => $id,
            'error' => [
                'code' => $code,
                'message' => $message,
            ],
        ]);
    }
}
