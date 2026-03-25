<?php

declare(strict_types=1);

namespace Tests;

use PHPUnit\Framework\TestCase;
use ServerLens\Config;

final class ConfigLoadTest extends TestCase
{
    public function testLoadValidConfig(): void
    {
        $config = $this->makeConfig([
            'server' => ['host' => '127.0.0.1', 'port' => 9600, 'transport' => 'stdio'],
            'auth' => ['tokens' => []],
        ]);

        $this->assertSame('127.0.0.1', $config->getServerHost());
        $this->assertSame(9600, $config->getServerPort());
        $this->assertSame('stdio', $config->getTransport());
    }

    public function testRejectsNonLocalhostHost(): void
    {
        $this->expectException(\RuntimeException::class);
        $this->expectExceptionMessageMatches('/server\.host must be localhost/');

        $this->makeConfig([
            'server' => ['host' => '0.0.0.0', 'port' => 9600, 'transport' => 'sse'],
            'auth' => ['tokens' => [['hash' => 'x', 'created' => '2026-01-01', 'expires' => '2030-01-01']]],
        ]);
    }

    public function testRejectsInvalidTransport(): void
    {
        $this->expectException(\RuntimeException::class);

        $this->makeConfig([
            'server' => ['host' => '127.0.0.1', 'port' => 9600, 'transport' => 'grpc'],
        ]);
    }

    public function testSseRequiresTokens(): void
    {
        $this->expectException(\RuntimeException::class);
        $this->expectExceptionMessageMatches('/tokens must not be empty/');

        $this->makeConfig([
            'server' => ['host' => '127.0.0.1', 'port' => 9600, 'transport' => 'sse'],
            'auth' => ['tokens' => []],
        ]);
    }

    public function testStdioAllowsEmptyTokens(): void
    {
        $config = $this->makeConfig([
            'server' => ['host' => '127.0.0.1', 'port' => 9600, 'transport' => 'stdio'],
            'auth' => ['tokens' => []],
        ]);

        $this->assertSame('stdio', $config->getTransport());
    }

    public function testDefaults(): void
    {
        $config = $this->makeConfig([
            'server' => ['host' => '127.0.0.1', 'transport' => 'stdio'],
        ]);

        $this->assertSame(9600, $config->getServerPort());
        $this->assertSame(60, $config->getRequestsPerMinute());
        $this->assertSame(5, $config->getMaxConcurrent());
        $this->assertTrue($config->isAuditEnabled());
    }

    public function testMissingFileThrows(): void
    {
        $this->expectException(\RuntimeException::class);
        Config::load('/nonexistent/path/config.yaml');
    }

    public function testGetNestedValues(): void
    {
        $config = $this->makeConfig([
            'server' => ['host' => '127.0.0.1', 'transport' => 'stdio'],
            'rate_limiting' => ['requests_per_minute' => 30, 'max_concurrent' => 3],
        ]);

        $this->assertSame(30, $config->getRequestsPerMinute());
        $this->assertSame(3, $config->getMaxConcurrent());
    }

    private function makeConfig(array $data): Config
    {
        $tmp = tempnam(sys_get_temp_dir(), 'sl_');
        file_put_contents($tmp, \Symfony\Component\Yaml\Yaml::dump($data, 10));
        $config = Config::load($tmp);
        unlink($tmp);
        return $config;
    }
}
