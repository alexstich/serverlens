<?php

declare(strict_types=1);

namespace Tests;

use PHPUnit\Framework\TestCase;
use ServerLens\Audit\AuditLogger;

final class AuditLoggerTest extends TestCase
{
    private string $logPath;

    protected function setUp(): void
    {
        $this->logPath = tempnam(sys_get_temp_dir(), 'sl_audit_');
    }

    protected function tearDown(): void
    {
        @unlink($this->logPath);
    }

    public function testLogsEntry(): void
    {
        $logger = new AuditLogger($this->logPath);
        $logger->log('127.0.0.1', 'logs_tail', ['source' => 'nginx'], true, 15);

        unset($logger);

        $content = file_get_contents($this->logPath);
        $entry = json_decode(trim($content), true);

        $this->assertSame('127.0.0.1', $entry['client_ip']);
        $this->assertSame('logs_tail', $entry['tool']);
        $this->assertSame('ok', $entry['result']['status']);
        $this->assertSame(15, $entry['result']['duration_ms']);
    }

    public function testSummarizesParamsWithoutValues(): void
    {
        $logger = new AuditLogger($this->logPath, logParams: false);
        $logger->log('127.0.0.1', 'db_query', [
            'database' => 'prod',
            'table' => 'users',
            'fields' => ['id', 'email', 'name'],
            'filters' => ['status' => ['eq' => 'active']],
            'limit' => 50,
            'query' => 'some search text',
        ], true, 42);

        unset($logger);

        $content = file_get_contents($this->logPath);
        $entry = json_decode(trim($content), true);
        $summary = $entry['params_summary'];

        $this->assertSame('prod', $summary['database']);
        $this->assertSame('users', $summary['table']);
        $this->assertSame(3, $summary['fields_count']);
        $this->assertTrue($summary['has_filters']);
        $this->assertSame(50, $summary['limit']);
        $this->assertSame(16, $summary['query_length']);

        $this->assertArrayNotHasKey('fields', $summary);
        $this->assertArrayNotHasKey('filters', $summary);
    }

    public function testLogsErrorStatus(): void
    {
        $logger = new AuditLogger($this->logPath);
        $logger->log('127.0.0.1', 'db_query', [], false, 5);

        unset($logger);

        $content = file_get_contents($this->logPath);
        $entry = json_decode(trim($content), true);

        $this->assertSame('error', $entry['result']['status']);
    }

    public function testLogParamsWhenEnabled(): void
    {
        $logger = new AuditLogger($this->logPath, logParams: true);
        $logger->log('127.0.0.1', 'logs_search', [
            'source' => 'nginx',
            'query' => 'error 500',
        ], true, 10);

        unset($logger);

        $content = file_get_contents($this->logPath);
        $entry = json_decode(trim($content), true);

        $this->assertSame('nginx', $entry['params_summary']['source']);
        $this->assertSame('error 500', $entry['params_summary']['query']);
    }
}
