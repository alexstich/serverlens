<?php

declare(strict_types=1);

namespace Tests;

use PHPUnit\Framework\TestCase;
use ServerLens\Config;
use ServerLens\Module\LogReader;

final class LogReaderTest extends TestCase
{
    private LogReader $reader;

    protected function setUp(): void
    {
        $this->reader = new LogReader($this->loadConfig());
    }

    public function testLogsList(): void
    {
        $result = $this->reader->handleToolCall('logs_list', []);
        $data = json_decode($result['content'][0]['text'], true);

        $this->assertCount(1, $data);
        $this->assertSame('test_log', $data[0]['name']);
        $this->assertTrue($data[0]['available']);
    }

    public function testLogsTail(): void
    {
        $result = $this->reader->handleToolCall('logs_tail', [
            'source' => 'test_log',
            'lines' => 3,
        ]);

        $text = $result['content'][0]['text'];
        $lines = explode("\n", $text);

        $this->assertCount(3, $lines);
        $this->assertStringContainsString('10:08:15', $lines[2]);
    }

    public function testLogsTailDefaultLines(): void
    {
        $result = $this->reader->handleToolCall('logs_tail', [
            'source' => 'test_log',
        ]);

        $text = $result['content'][0]['text'];
        $lines = array_filter(explode("\n", $text), fn($l) => $l !== '');

        $this->assertSame(8, count($lines));
    }

    public function testLogsSearch(): void
    {
        $result = $this->reader->handleToolCall('logs_search', [
            'source' => 'test_log',
            'query' => 'ERROR',
        ]);

        $text = $result['content'][0]['text'];
        $lines = explode("\n", $text);

        $this->assertCount(2, $lines);
        $this->assertStringContainsString('Connection timeout', $lines[0]);
        $this->assertStringContainsString('Database connection lost', $lines[1]);
    }

    public function testLogsSearchRegex(): void
    {
        $result = $this->reader->handleToolCall('logs_search', [
            'source' => 'test_log',
            'query' => 'GET|POST',
            'regex' => true,
        ]);

        $text = $result['content'][0]['text'];
        $lines = explode("\n", $text);

        $this->assertCount(3, $lines);
    }

    public function testLogsSearchNoMatch(): void
    {
        $result = $this->reader->handleToolCall('logs_search', [
            'source' => 'test_log',
            'query' => 'NONEXISTENT_STRING_XYZ',
        ]);

        $text = $result['content'][0]['text'];
        $this->assertStringContainsString('No matches', $text);
    }

    public function testLogsCount(): void
    {
        $result = $this->reader->handleToolCall('logs_count', [
            'source' => 'test_log',
        ]);

        $data = json_decode($result['content'][0]['text'], true);

        $this->assertSame('test_log', $data['source']);
        $this->assertSame(8, $data['lines']);
        $this->assertGreaterThan(0, $data['size_bytes']);
    }

    public function testLogsTimeRange(): void
    {
        $result = $this->reader->handleToolCall('logs_time_range', [
            'source' => 'test_log',
            'from' => '2026-03-25 10:02:00',
            'to' => '2026-03-25 10:06:30',
        ]);

        $text = $result['content'][0]['text'];
        $lines = explode("\n", $text);

        $this->assertGreaterThanOrEqual(2, count($lines));
        $this->assertStringContainsString('ERROR', $lines[0]);
    }

    public function testUnknownSourceReturnsError(): void
    {
        $result = $this->reader->handleToolCall('logs_tail', [
            'source' => 'nonexistent',
        ]);

        $this->assertTrue($result['isError']);
    }

    public function testEmptyQueryReturnsError(): void
    {
        $result = $this->reader->handleToolCall('logs_search', [
            'source' => 'test_log',
            'query' => '',
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
