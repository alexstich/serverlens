<?php

declare(strict_types=1);

namespace ServerLens\Auth;

use ServerLens\Config;

final class TokenAuth
{
    /** @var array<array{hash: string, created: string, expires: string}> */
    private array $tokens;

    private int $maxFailed;
    private int $lockoutMinutes;

    /** @var array<string, array{count: int, locked_until: int}> */
    private array $failedAttempts = [];

    public function __construct(Config $config)
    {
        $this->tokens = $config->getTokens();
        $this->maxFailed = $config->getMaxFailedAttempts();
        $this->lockoutMinutes = $config->getLockoutMinutes();
    }

    public function verify(string $authHeader, string $clientIp = '127.0.0.1'): bool
    {
        if ($this->isLockedOut($clientIp)) {
            return false;
        }

        if (!str_starts_with($authHeader, 'Bearer ')) {
            $this->recordFailure($clientIp);
            return false;
        }

        $token = substr($authHeader, 7);
        if (empty($token)) {
            $this->recordFailure($clientIp);
            return false;
        }

        foreach ($this->tokens as $entry) {
            $hash = $entry['hash'] ?? '';
            $expires = $entry['expires'] ?? '';

            if ($expires && strtotime($expires) < time()) {
                continue;
            }

            if (password_verify($token, $hash)) {
                $this->clearFailures($clientIp);
                return true;
            }
        }

        $this->recordFailure($clientIp);
        return false;
    }

    public static function generateToken(): string
    {
        return 'sl_' . bin2hex(random_bytes(32));
    }

    public static function hashToken(string $token): string
    {
        return password_hash($token, PASSWORD_ARGON2ID, [
            'memory_cost' => 65536,
            'time_cost' => 4,
            'threads' => 1,
        ]);
    }

    private function isLockedOut(string $clientIp): bool
    {
        if (!isset($this->failedAttempts[$clientIp])) {
            return false;
        }

        $entry = $this->failedAttempts[$clientIp];
        $lockedUntil = $entry['locked_until'] ?? 0;

        if ($lockedUntil > 0 && $lockedUntil > time()) {
            return true;
        }

        if ($lockedUntil > 0 && $lockedUntil <= time()) {
            unset($this->failedAttempts[$clientIp]);
        }

        return false;
    }

    private function recordFailure(string $clientIp): void
    {
        if (!isset($this->failedAttempts[$clientIp])) {
            $this->failedAttempts[$clientIp] = ['count' => 0, 'locked_until' => 0];
        }

        $this->failedAttempts[$clientIp]['count']++;

        if ($this->failedAttempts[$clientIp]['count'] >= $this->maxFailed) {
            $this->failedAttempts[$clientIp]['locked_until'] =
                time() + ($this->lockoutMinutes * 60);
        }
    }

    private function clearFailures(string $clientIp): void
    {
        unset($this->failedAttempts[$clientIp]);
    }
}
