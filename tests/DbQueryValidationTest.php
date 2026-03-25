<?php

declare(strict_types=1);

namespace Tests;

use PHPUnit\Framework\TestCase;
use ServerLens\Config;
use ServerLens\Module\DbQuery;

final class DbQueryValidationTest extends TestCase
{
    private DbQuery $db;

    protected function setUp(): void
    {
        $this->db = new DbQuery($this->makeConfig());
    }

    public function testDbListShowsTables(): void
    {
        $result = $this->db->handleToolCall('db_list', []);
        $data = json_decode($result['content'][0]['text'], true);

        $this->assertCount(1, $data);
        $this->assertSame('test_db', $data[0]['database']);
        $this->assertCount(1, $data[0]['tables']);
        $this->assertSame('users', $data[0]['tables'][0]['name']);
    }

    public function testDbDescribe(): void
    {
        $result = $this->db->handleToolCall('db_describe', [
            'database' => 'test_db',
            'table' => 'users',
        ]);

        $data = json_decode($result['content'][0]['text'], true);

        $this->assertSame('test_db', $data['database']);
        $this->assertSame('users', $data['table']);
        $this->assertContains('id', $data['allowed_fields']);
        $this->assertContains('email', $data['allowed_fields']);
        $this->assertContains('password_hash', $data['denied_fields']);
    }

    public function testUnknownDatabaseError(): void
    {
        $result = $this->db->handleToolCall('db_query', [
            'database' => 'unknown',
            'table' => 'users',
        ]);

        $this->assertTrue($result['isError']);
    }

    public function testUnknownTableError(): void
    {
        $result = $this->db->handleToolCall('db_query', [
            'database' => 'test_db',
            'table' => 'nonexistent',
        ]);

        $this->assertTrue($result['isError']);
    }

    public function testDeniedFieldRejected(): void
    {
        $result = $this->db->handleToolCall('db_query', [
            'database' => 'test_db',
            'table' => 'users',
            'fields' => ['id', 'password_hash'],
        ]);

        $this->assertTrue($result['isError']);
        $this->assertStringContainsString('not allowed', $result['content'][0]['text']);
    }

    public function testDisallowedFieldRejected(): void
    {
        $result = $this->db->handleToolCall('db_query', [
            'database' => 'test_db',
            'table' => 'users',
            'fields' => ['id', 'secret_column'],
        ]);

        $this->assertTrue($result['isError']);
        $this->assertStringContainsString('not allowed', $result['content'][0]['text']);
    }

    public function testDisallowedFilterRejected(): void
    {
        $result = $this->db->handleToolCall('db_query', [
            'database' => 'test_db',
            'table' => 'users',
            'filters' => ['secret_field' => ['eq' => 'x']],
        ]);

        $this->assertTrue($result['isError']);
        $this->assertStringContainsString('not allowed', $result['content'][0]['text']);
    }

    public function testInvalidFilterOperator(): void
    {
        $result = $this->db->handleToolCall('db_query', [
            'database' => 'test_db',
            'table' => 'users',
            'filters' => ['id' => ['INVALID_OP' => 1]],
        ]);

        $this->assertTrue($result['isError']);
        $this->assertStringContainsString('Invalid filter operator', $result['content'][0]['text']);
    }

    public function testInListTooMany(): void
    {
        $values = range(1, 51);
        $result = $this->db->handleToolCall('db_query', [
            'database' => 'test_db',
            'table' => 'users',
            'filters' => ['id' => ['in' => $values]],
        ]);

        $this->assertTrue($result['isError']);
        $this->assertStringContainsString('50 values', $result['content'][0]['text']);
    }

    public function testDisallowedOrderByRejected(): void
    {
        $result = $this->db->handleToolCall('db_query', [
            'database' => 'test_db',
            'table' => 'users',
            'order_by' => ['email'],
        ]);

        $this->assertTrue($result['isError']);
        $this->assertStringContainsString('not allowed', $result['content'][0]['text']);
    }

    private function makeConfig(): Config
    {
        $data = [
            'server' => ['host' => '127.0.0.1', 'transport' => 'stdio'],
            'databases' => [
                'connections' => [[
                    'name' => 'test_db',
                    'host' => 'localhost',
                    'port' => 5432,
                    'database' => 'test',
                    'user' => 'test',
                    'password_env' => '',
                    'tables' => [[
                        'name' => 'users',
                        'allowed_fields' => ['id', 'email', 'created_at', 'is_active'],
                        'denied_fields' => ['password_hash', 'reset_token'],
                        'max_rows' => 100,
                        'allowed_filters' => ['id', 'email', 'is_active', 'created_at'],
                        'allowed_order_by' => ['id', 'created_at'],
                    ]],
                ]],
            ],
        ];
        $tmp = tempnam(sys_get_temp_dir(), 'sl_');
        file_put_contents($tmp, \Symfony\Component\Yaml\Yaml::dump($data, 10));
        $config = Config::load($tmp);
        unlink($tmp);
        return $config;
    }
}
