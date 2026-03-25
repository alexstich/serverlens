<?php

declare(strict_types=1);

namespace Tests;

use PHPUnit\Framework\TestCase;
use ServerLens\Config;
use ServerLens\Module\ConfigReader;

final class ConfigReaderTest extends TestCase
{
    private ConfigReader $reader;

    protected function setUp(): void
    {
        $this->reader = new ConfigReader($this->loadConfig());
    }

    public function testConfigList(): void
    {
        $result = $this->reader->handleToolCall('config_list', []);
        $data = json_decode($result['content'][0]['text'], true);

        $this->assertCount(1, $data);
        $this->assertSame('test_conf', $data[0]['name']);
        $this->assertTrue($data[0]['available']);
    }

    public function testConfigReadRedactsSecrets(): void
    {
        $result = $this->reader->handleToolCall('config_read', [
            'source' => 'test_conf',
        ]);

        $text = $result['content'][0]['text'];

        $this->assertStringContainsString('listen 80', $text);
        $this->assertStringContainsString('server_name example.com', $text);

        $this->assertStringNotContainsString('supersecret123', $text);
        $this->assertStringNotContainsString('sk-abc123def456', $text);
        $this->assertStringContainsString('[REDACTED]', $text);
    }

    public function testConfigSearch(): void
    {
        $result = $this->reader->handleToolCall('config_search', [
            'source' => 'test_conf',
            'query' => 'listen',
        ]);

        $text = $result['content'][0]['text'];
        $this->assertStringContainsString('listen 80', $text);
    }

    public function testConfigSearchNoMatch(): void
    {
        $result = $this->reader->handleToolCall('config_search', [
            'source' => 'test_conf',
            'query' => 'NONEXISTENT',
        ]);

        $text = $result['content'][0]['text'];
        $this->assertStringContainsString('No matches', $text);
    }

    public function testUnknownSourceError(): void
    {
        $result = $this->reader->handleToolCall('config_read', [
            'source' => 'unknown',
        ]);

        $this->assertTrue($result['isError']);
    }

    private function loadConfig(): Config
    {
        $fixtureDir = __DIR__ . '/fixtures';
        $configContent = file_get_contents($fixtureDir . '/config.yaml');
        $configContent = str_replace('FIXTURE_PATH', $fixtureDir, $configContent);

        $tmp = tempnam(sys_get_temp_dir(), 'sl_');
        file_put_contents($tmp, $configContent);
        $config = Config::load($tmp);
        unlink($tmp);
        return $config;
    }
}
