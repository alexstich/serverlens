<?php

declare(strict_types=1);

namespace Tests;

use PHPUnit\Framework\TestCase;
use ServerLens\Config;
use ServerLens\Module\DbQuery;

final class DbAggregateTest extends TestCase
{
    private DbQuery $db;

    protected function setUp(): void
    {
        $this->db = new DbQuery($this->makeConfig());
    }

    public function testUnknownDatabaseError(): void
    {
        $result = $this->db->handleToolCall('db_aggregate', [
            'database' => 'unknown',
            'table' => 'users',
            'group_by' => ['email'],
        ]);

        $this->assertTrue($result['isError']);
    }

    public function testUnknownTableError(): void
    {
        $result = $this->db->handleToolCall('db_aggregate', [
            'database' => 'test_db',
            'table' => 'nonexistent',
            'group_by' => ['email'],
        ]);

        $this->assertTrue($result['isError']);
    }

    public function testEmptyGroupByRejected(): void
    {
        $result = $this->db->handleToolCall('db_aggregate', [
            'database' => 'test_db',
            'table' => 'users',
            'group_by' => [],
        ]);

        $this->assertTrue($result['isError']);
        $this->assertStringContainsString('group_by', $result['content'][0]['text']);
    }

    public function testDisallowedGroupByFieldRejected(): void
    {
        $result = $this->db->handleToolCall('db_aggregate', [
            'database' => 'test_db',
            'table' => 'users',
            'group_by' => ['email', 'secret_column'],
        ]);

        $this->assertTrue($result['isError']);
        $this->assertStringContainsString('not allowed', $result['content'][0]['text']);
    }

    public function testDeniedGroupByFieldRejected(): void
    {
        $result = $this->db->handleToolCall('db_aggregate', [
            'database' => 'test_db',
            'table' => 'users',
            'group_by' => ['password_hash'],
        ]);

        $this->assertTrue($result['isError']);
    }

    public function testMalformedGroupByIdentifierRejected(): void
    {
        $result = $this->db->handleToolCall('db_aggregate', [
            'database' => 'test_db',
            'table' => 'users',
            'group_by' => ['email"; DROP TABLE users; --'],
        ]);

        $this->assertTrue($result['isError']);
        $this->assertStringContainsString('Invalid group_by field', $result['content'][0]['text']);
    }

    public function testUnsupportedAggregateRejected(): void
    {
        $result = $this->db->handleToolCall('db_aggregate', [
            'database' => 'test_db',
            'table' => 'users',
            'group_by' => ['email'],
            'aggregate' => 'sum',
        ]);

        $this->assertTrue($result['isError']);
        $this->assertStringContainsString('Invalid aggregate function', $result['content'][0]['text']);
    }

    public function testInvalidOrderRejected(): void
    {
        $result = $this->db->handleToolCall('db_aggregate', [
            'database' => 'test_db',
            'table' => 'users',
            'group_by' => ['email'],
            'order' => 'email; DROP TABLE users',
        ]);

        $this->assertTrue($result['isError']);
        $this->assertStringContainsString('Invalid order', $result['content'][0]['text']);
    }

    public function testInvalidHavingMinCountRejected(): void
    {
        foreach ([0, -5, 'abc'] as $having) {
            $result = $this->db->handleToolCall('db_aggregate', [
                'database' => 'test_db',
                'table' => 'users',
                'group_by' => ['email'],
                'having_min_count' => $having,
            ]);

            $this->assertTrue($result['isError'], "having_min_count '{$having}' must be rejected");
            $this->assertStringContainsString('having_min_count', $result['content'][0]['text']);
        }
    }

    public function testDisallowedFilterRejected(): void
    {
        $result = $this->db->handleToolCall('db_aggregate', [
            'database' => 'test_db',
            'table' => 'users',
            'group_by' => ['email'],
            'filters' => ['secret_field' => ['eq' => 'x']],
        ]);

        $this->assertTrue($result['isError']);
        $this->assertStringContainsString('not allowed', $result['content'][0]['text']);
    }

    public function testInvalidFilterOperatorRejected(): void
    {
        $result = $this->db->handleToolCall('db_aggregate', [
            'database' => 'test_db',
            'table' => 'users',
            'group_by' => ['email'],
            'filters' => ['id' => ['INVALID_OP' => 1]],
        ]);

        $this->assertTrue($result['isError']);
        $this->assertStringContainsString('Invalid filter operator', $result['content'][0]['text']);
    }

    public function testValidParamsPassValidation(): void
    {
        // Validation passes; the call then fails on the (unconfigured) DB connection,
        // not on parameter checks.
        $result = $this->db->handleToolCall('db_aggregate', [
            'database' => 'test_db',
            'table' => 'users',
            'group_by' => ['email'],
            'having_min_count' => 2,
            'order' => 'count_desc',
        ]);

        $this->assertTrue($result['isError']);
        $this->assertStringContainsString('authentication failed', $result['content'][0]['text']);
    }

    public function testSqlGenerationPostgres(): void
    {
        $params = [];
        $sql = $this->buildSql(
            groupBy: ['gis_guid'],
            filters: ['is_active' => ['eq' => true]],
            havingMinCount: 2,
            order: 'count_desc',
            limit: 50,
            maxRows: 100,
            driver: 'postgresql',
            params: $params,
        );

        $this->assertSame(
            'SELECT "gis_guid", COUNT(*) AS count FROM "users"'
            . ' WHERE "is_active" = :p0'
            . ' GROUP BY "gis_guid"'
            . ' HAVING COUNT(*) >= :_having'
            . ' ORDER BY COUNT(*) DESC'
            . ' LIMIT :_limit',
            $sql,
        );
        $this->assertSame([':p0' => true, ':_having' => 2, ':_limit' => 50], $params);
    }

    public function testSqlGenerationMultipleGroupByAscNoHaving(): void
    {
        $params = [];
        $sql = $this->buildSql(
            groupBy: ['email', 'is_active'],
            filters: [],
            havingMinCount: null,
            order: 'count_asc',
            limit: 10,
            maxRows: 100,
            driver: 'postgresql',
            params: $params,
        );

        $this->assertSame(
            'SELECT "email", "is_active", COUNT(*) AS count FROM "users"'
            . ' GROUP BY "email", "is_active"'
            . ' ORDER BY COUNT(*) ASC'
            . ' LIMIT :_limit',
            $sql,
        );
        $this->assertSame([':_limit' => 10], $params);
    }

    public function testSqlGenerationMysqlQuoting(): void
    {
        $params = [];
        $sql = $this->buildSql(
            groupBy: ['email'],
            filters: [],
            havingMinCount: null,
            order: 'count_desc',
            limit: 10,
            maxRows: 100,
            driver: 'mysql',
            params: $params,
        );

        $this->assertStringContainsString('SELECT `email`, COUNT(*) AS count FROM `users`', $sql);
        $this->assertStringContainsString('GROUP BY `email`', $sql);
    }

    public function testSqlGenerationCapsLimitAtMaxRows(): void
    {
        $params = [];
        $this->buildSql(
            groupBy: ['email'],
            filters: [],
            havingMinCount: null,
            order: 'count_desc',
            limit: 5000,
            maxRows: 100,
            driver: 'postgresql',
            params: $params,
        );

        $this->assertSame(100, $params[':_limit']);
    }

    private function buildSql(
        array $groupBy,
        array $filters,
        ?int $havingMinCount,
        string $order,
        int $limit,
        int $maxRows,
        string $driver,
        array &$params,
    ): string {
        $method = new \ReflectionMethod(DbQuery::class, 'buildAggregateSql');

        $args = ['users', $groupBy, $filters, $havingMinCount, $order, $limit, $maxRows, $driver, &$params];

        return $method->invokeArgs($this->db, $args);
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
                        'allowed_fields' => ['id', 'email', 'gis_guid', 'created_at', 'is_active'],
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
