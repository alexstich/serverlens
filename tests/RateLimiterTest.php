<?php

declare(strict_types=1);

namespace Tests;

use PHPUnit\Framework\TestCase;
use ServerLens\Auth\RateLimiter;

final class RateLimiterTest extends TestCase
{
    public function testAllowsUnderLimit(): void
    {
        $limiter = new RateLimiter(requestsPerMinute: 5, maxConcurrent: 10);

        for ($i = 0; $i < 5; $i++) {
            $this->assertTrue($limiter->allow('client1'), "Request {$i} should be allowed");
        }
    }

    public function testBlocksOverLimit(): void
    {
        $limiter = new RateLimiter(requestsPerMinute: 3, maxConcurrent: 10);

        $this->assertTrue($limiter->allow('client1'));
        $this->assertTrue($limiter->allow('client1'));
        $this->assertTrue($limiter->allow('client1'));
        $this->assertFalse($limiter->allow('client1'), 'Fourth request should be blocked');
    }

    public function testDifferentClientsIndependent(): void
    {
        $limiter = new RateLimiter(requestsPerMinute: 2, maxConcurrent: 10);

        $this->assertTrue($limiter->allow('client1'));
        $this->assertTrue($limiter->allow('client1'));
        $this->assertFalse($limiter->allow('client1'));

        $this->assertTrue($limiter->allow('client2'), 'Different client should not be affected');
    }

    public function testConcurrentLimit(): void
    {
        $limiter = new RateLimiter(requestsPerMinute: 100, maxConcurrent: 2);

        $limiter->incrementConcurrent();
        $limiter->incrementConcurrent();

        $this->assertFalse($limiter->allow('client1'), 'Should block at max concurrent');

        $limiter->decrementConcurrent();
        $this->assertTrue($limiter->allow('client1'), 'Should allow after decrement');
    }
}
