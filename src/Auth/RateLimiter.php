<?php

declare(strict_types=1);

namespace ServerLens\Auth;

final class RateLimiter
{
    /** @var array<string, array<int>> timestamps of requests */
    private array $requests = [];

    private int $concurrentCount = 0;

    public function __construct(
        private readonly int $requestsPerMinute = 60,
        private readonly int $maxConcurrent = 5,
    ) {}

    public function allow(string $clientId): bool
    {
        $this->cleanup($clientId);

        $now = time();
        $count = count($this->requests[$clientId] ?? []);

        if ($count >= $this->requestsPerMinute) {
            return false;
        }

        if ($this->concurrentCount >= $this->maxConcurrent) {
            return false;
        }

        $this->requests[$clientId][] = $now;

        return true;
    }

    public function incrementConcurrent(): void
    {
        $this->concurrentCount++;
    }

    public function decrementConcurrent(): void
    {
        $this->concurrentCount = max(0, $this->concurrentCount - 1);
    }

    private function cleanup(string $clientId): void
    {
        if (!isset($this->requests[$clientId])) {
            return;
        }

        $threshold = time() - 60;
        $this->requests[$clientId] = array_values(
            array_filter($this->requests[$clientId], fn(int $ts) => $ts > $threshold)
        );
    }
}
