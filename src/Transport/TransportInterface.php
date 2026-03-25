<?php

declare(strict_types=1);

namespace ServerLens\Transport;

interface TransportInterface
{
    /**
     * @param callable(array $message, string $clientIp): ?array $handler  JSON-RPC message handler
     */
    public function onMessage(callable $handler): void;

    public function start(): void;
}
