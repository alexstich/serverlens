<?php

declare(strict_types=1);

namespace ServerLens\Module;

use ServerLens\Config;
use ServerLens\Mcp\Tool;

final class DbQuery implements ModuleInterface
{
    /** @var array<string, array{dsn: string, user: string, password: string, tables: array}> */
    private array $connections = [];

    /** @var array<string, \PDO> */
    private array $pdoCache = [];

    public function __construct(Config $config)
    {
        foreach ($config->getDatabaseConnections() as $conn) {
            $passwordEnv = $conn['password_env'] ?? '';
            $password = $passwordEnv ? (getenv($passwordEnv) ?: '') : '';

            $host = $conn['host'] ?? 'localhost';
            $port = $conn['port'] ?? 5432;
            $database = $conn['database'] ?? '';
            $user = $conn['user'] ?? '';

            $dsn = "pgsql:host={$host};port={$port};dbname={$database}";

            $tables = [];
            foreach ($conn['tables'] ?? [] as $table) {
                $tables[$table['name']] = [
                    'allowed_fields' => $table['allowed_fields'] ?? ['*'],
                    'denied_fields' => $table['denied_fields'] ?? [],
                    'max_rows' => (int) ($table['max_rows'] ?? 1000),
                    'allowed_filters' => $table['allowed_filters'] ?? [],
                    'allowed_order_by' => $table['allowed_order_by'] ?? [],
                ];
            }

            $this->connections[$conn['name']] = [
                'dsn' => $dsn,
                'user' => $user,
                'password' => $password,
                'tables' => $tables,
            ];
        }
    }

    public function getTools(): array
    {
        return [
            new Tool('db_list', 'List databases, tables, and available fields', [
                'type' => 'object',
                'properties' => new \stdClass(),
            ]),
            new Tool('db_describe', 'Describe table structure (allowed fields)', [
                'type' => 'object',
                'properties' => [
                    'database' => ['type' => 'string', 'description' => 'Database connection name'],
                    'table' => ['type' => 'string', 'description' => 'Table name'],
                ],
                'required' => ['database', 'table'],
            ]),
            new Tool('db_query', 'Query records with structured filters (no raw SQL)', [
                'type' => 'object',
                'properties' => [
                    'database' => ['type' => 'string', 'description' => 'Database connection name'],
                    'table' => ['type' => 'string', 'description' => 'Table name'],
                    'fields' => [
                        'type' => 'array',
                        'items' => ['type' => 'string'],
                        'description' => 'Fields to select',
                    ],
                    'filters' => [
                        'type' => 'object',
                        'description' => 'Filter conditions: {field: {op: value}}. Ops: eq, neq, gt, gte, lt, lte, in, like, is_null',
                    ],
                    'order_by' => [
                        'type' => 'array',
                        'items' => ['type' => 'string'],
                        'description' => 'Order by fields. Prefix with - for DESC.',
                    ],
                    'limit' => ['type' => 'integer', 'description' => 'Max rows to return'],
                    'offset' => ['type' => 'integer', 'description' => 'Offset for pagination', 'default' => 0],
                ],
                'required' => ['database', 'table'],
            ]),
            new Tool('db_count', 'Count records matching filters', [
                'type' => 'object',
                'properties' => [
                    'database' => ['type' => 'string', 'description' => 'Database connection name'],
                    'table' => ['type' => 'string', 'description' => 'Table name'],
                    'filters' => [
                        'type' => 'object',
                        'description' => 'Filter conditions',
                    ],
                ],
                'required' => ['database', 'table'],
            ]),
            new Tool('db_stats', 'Get basic statistics for a numeric field (COUNT, MIN, MAX, AVG)', [
                'type' => 'object',
                'properties' => [
                    'database' => ['type' => 'string', 'description' => 'Database connection name'],
                    'table' => ['type' => 'string', 'description' => 'Table name'],
                    'field' => ['type' => 'string', 'description' => 'Field name'],
                ],
                'required' => ['database', 'table', 'field'],
            ]),
        ];
    }

    public function handleToolCall(string $name, array $arguments): array
    {
        return match ($name) {
            'db_list' => $this->listDatabases(),
            'db_describe' => $this->describe($arguments),
            'db_query' => $this->query($arguments),
            'db_count' => $this->count($arguments),
            'db_stats' => $this->stats($arguments),
            default => $this->error("Unknown tool: {$name}"),
        };
    }

    private function listDatabases(): array
    {
        $result = [];
        foreach ($this->connections as $name => $conn) {
            $tables = [];
            foreach ($conn['tables'] as $tName => $tConfig) {
                $tables[] = [
                    'name' => $tName,
                    'allowed_fields' => $tConfig['allowed_fields'],
                    'max_rows' => $tConfig['max_rows'],
                ];
            }

            $connStatus = 'untested';
            $connError = null;
            $hasPassword = !empty($conn['password']);
            try {
                $pdo = $this->getPdo($name);
                $pdo->query('SELECT 1');
                $connStatus = 'ok';
            } catch (\PDOException $e) {
                $connStatus = 'error';
                $connError = $this->formatDbError($e);
            }

            $entry = [
                'database' => $name,
                'connection_status' => $connStatus,
                'has_password' => $hasPassword,
                'tables' => $tables,
            ];
            if ($connError) {
                $entry['connection_error'] = $connError;
            }

            $result[] = $entry;
        }

        return $this->ok(json_encode($result, JSON_PRETTY_PRINT | JSON_UNESCAPED_UNICODE));
    }

    private function describe(array $args): array
    {
        $dbName = $args['database'] ?? '';
        $tableName = $args['table'] ?? '';

        $tableConfig = $this->getTableConfig($dbName, $tableName);
        if ($tableConfig === null) {
            return $this->error("Unknown database or table");
        }

        $info = [
            'database' => $dbName,
            'table' => $tableName,
            'allowed_fields' => $tableConfig['allowed_fields'],
            'denied_fields' => $tableConfig['denied_fields'],
            'max_rows' => $tableConfig['max_rows'],
            'allowed_filters' => $tableConfig['allowed_filters'],
            'allowed_order_by' => $tableConfig['allowed_order_by'],
        ];

        return $this->ok(json_encode($info, JSON_PRETTY_PRINT | JSON_UNESCAPED_UNICODE));
    }

    private function query(array $args): array
    {
        $dbName = $args['database'] ?? '';
        $tableName = $args['table'] ?? '';
        $fields = $args['fields'] ?? null;
        $filters = $args['filters'] ?? [];
        $orderBy = $args['order_by'] ?? [];
        $limit = (int) ($args['limit'] ?? 100);
        $offset = (int) ($args['offset'] ?? 0);

        $tableConfig = $this->getTableConfig($dbName, $tableName);
        if ($tableConfig === null) {
            return $this->error("Unknown database or table");
        }

        $allowedFields = $this->resolveAllowedFields($tableConfig);
        if ($fields === null) {
            $fields = $allowedFields;
        }

        $validation = $this->validateQueryParams($fields, $filters, $orderBy, $tableConfig);
        if ($validation !== null) {
            return $this->error($validation);
        }

        $limit = min($limit, $tableConfig['max_rows']);
        $offset = max(0, $offset);

        try {
            $pdo = $this->getPdo($dbName);

            $quotedFields = array_map(fn($f) => $this->quoteIdentifier($f), $fields);
            $quotedTable = $this->quoteIdentifier($tableName);

            $sql = "SELECT " . implode(', ', $quotedFields) . " FROM {$quotedTable}";
            $params = [];

            $whereClause = $this->buildWhereClause($filters, $params);
            if ($whereClause) {
                $sql .= " WHERE {$whereClause}";
            }

            if (!empty($orderBy)) {
                $orderParts = [];
                foreach ($orderBy as $field) {
                    $dir = 'ASC';
                    if (str_starts_with($field, '-')) {
                        $dir = 'DESC';
                        $field = substr($field, 1);
                    }
                    $orderParts[] = $this->quoteIdentifier($field) . " {$dir}";
                }
                $sql .= " ORDER BY " . implode(', ', $orderParts);
            }

            $sql .= " LIMIT :_limit OFFSET :_offset";
            $params[':_limit'] = $limit;
            $params[':_offset'] = $offset;

            $stmt = $pdo->prepare($sql);
            $stmt->execute($params);
            $rows = $stmt->fetchAll(\PDO::FETCH_ASSOC);

            $result = [
                'database' => $dbName,
                'table' => $tableName,
                'rows_returned' => count($rows),
                'offset' => $offset,
                'limit' => $limit,
                'data' => $rows,
            ];

            return $this->ok(json_encode($result, JSON_PRETTY_PRINT | JSON_UNESCAPED_UNICODE));
        } catch (\PDOException $e) {
            fwrite(STDERR, "[ServerLens] DB error: {$e->getMessage()}\n");
            return $this->error($this->formatDbError($e));
        }
    }

    private function count(array $args): array
    {
        $dbName = $args['database'] ?? '';
        $tableName = $args['table'] ?? '';
        $filters = $args['filters'] ?? [];

        $tableConfig = $this->getTableConfig($dbName, $tableName);
        if ($tableConfig === null) {
            return $this->error("Unknown database or table");
        }

        $validation = $this->validateFilters($filters, $tableConfig);
        if ($validation !== null) {
            return $this->error($validation);
        }

        try {
            $pdo = $this->getPdo($dbName);
            $quotedTable = $this->quoteIdentifier($tableName);
            $params = [];

            $sql = "SELECT COUNT(*) as count FROM {$quotedTable}";
            $whereClause = $this->buildWhereClause($filters, $params);
            if ($whereClause) {
                $sql .= " WHERE {$whereClause}";
            }

            $stmt = $pdo->prepare($sql);
            $stmt->execute($params);
            $row = $stmt->fetch(\PDO::FETCH_ASSOC);

            $result = [
                'database' => $dbName,
                'table' => $tableName,
                'count' => (int) ($row['count'] ?? 0),
            ];

            return $this->ok(json_encode($result, JSON_PRETTY_PRINT));
        } catch (\PDOException $e) {
            fwrite(STDERR, "[ServerLens] DB error: {$e->getMessage()}\n");
            return $this->error($this->formatDbError($e));
        }
    }

    private function stats(array $args): array
    {
        $dbName = $args['database'] ?? '';
        $tableName = $args['table'] ?? '';
        $field = $args['field'] ?? '';

        $tableConfig = $this->getTableConfig($dbName, $tableName);
        if ($tableConfig === null) {
            return $this->error("Unknown database or table");
        }

        $allowedFields = $this->resolveAllowedFields($tableConfig);
        if (!in_array($field, $allowedFields, true)) {
            return $this->error("Field not allowed: {$field}");
        }

        try {
            $pdo = $this->getPdo($dbName);
            $quotedTable = $this->quoteIdentifier($tableName);
            $quotedField = $this->quoteIdentifier($field);

            $sql = "SELECT 
                COUNT({$quotedField}) as count,
                MIN({$quotedField}) as min,
                MAX({$quotedField}) as max,
                AVG({$quotedField}::numeric) as avg
                FROM {$quotedTable}";

            $stmt = $pdo->query($sql);
            $row = $stmt->fetch(\PDO::FETCH_ASSOC);

            $result = [
                'database' => $dbName,
                'table' => $tableName,
                'field' => $field,
                'count' => (int) ($row['count'] ?? 0),
                'min' => $row['min'] ?? null,
                'max' => $row['max'] ?? null,
                'avg' => $row['avg'] !== null ? round((float) $row['avg'], 4) : null,
            ];

            return $this->ok(json_encode($result, JSON_PRETTY_PRINT));
        } catch (\PDOException $e) {
            fwrite(STDERR, "[ServerLens] DB error: {$e->getMessage()}\n");
            return $this->error($this->formatDbError($e));
        }
    }

    private function getTableConfig(string $dbName, string $tableName): ?array
    {
        if (!isset($this->connections[$dbName])) {
            return null;
        }

        return $this->connections[$dbName]['tables'][$tableName] ?? null;
    }

    private function resolveAllowedFields(array $tableConfig): array
    {
        $allowed = $tableConfig['allowed_fields'];
        if ($allowed === ['*'] || $allowed === '*') {
            return ['*'];
        }

        $denied = $tableConfig['denied_fields'];
        return array_values(array_diff($allowed, $denied));
    }

    private function validateQueryParams(array $fields, array $filters, array $orderBy, array $tableConfig): ?string
    {
        $allowedFields = $this->resolveAllowedFields($tableConfig);
        $isWildcard = $allowedFields === ['*'];

        if (!$isWildcard) {
            foreach ($fields as $field) {
                if (!in_array($field, $allowedFields, true)) {
                    return "Field not allowed: {$field}";
                }
            }
        }

        $deniedFields = $tableConfig['denied_fields'];
        foreach ($fields as $field) {
            if (in_array($field, $deniedFields, true)) {
                return "Access denied to field: {$field}";
            }
        }

        $validationError = $this->validateFilters($filters, $tableConfig);
        if ($validationError !== null) {
            return $validationError;
        }

        $allowedOrderBy = $tableConfig['allowed_order_by'];
        foreach ($orderBy as $ob) {
            $field = ltrim($ob, '-');
            if (!empty($allowedOrderBy) && !in_array($field, $allowedOrderBy, true)) {
                $allowed = implode(', ', $allowedOrderBy);
                return "Order by field not allowed: {$field}. Allowed: [{$allowed}]";
            }
        }

        return null;
    }

    private function validateFilters(array $filters, array $tableConfig): ?string
    {
        $allowedFilters = $tableConfig['allowed_filters'];

        foreach ($filters as $field => $conditions) {
            if (!empty($allowedFilters) && !in_array($field, $allowedFilters, true)) {
                $allowed = implode(', ', $allowedFilters);
                return "Filter on field not allowed: {$field}. Allowed: [{$allowed}]";
            }

            if (!is_array($conditions)) {
                return "Invalid filter format for field: {$field}";
            }

            $validOps = ['eq', 'neq', 'gt', 'gte', 'lt', 'lte', 'in', 'like', 'is_null'];
            foreach ($conditions as $op => $value) {
                if (!in_array($op, $validOps, true)) {
                    return "Invalid filter operator: {$op}";
                }

                if ($op === 'in' && is_array($value) && count($value) > 50) {
                    return "IN operator limited to 50 values";
                }

                if ($op !== 'in' && $op !== 'is_null' && !is_scalar($value)) {
                    return "Filter values must be scalar";
                }
            }
        }

        return null;
    }

    private function buildWhereClause(array $filters, array &$params): string
    {
        $conditions = [];
        $paramIdx = 0;

        foreach ($filters as $field => $ops) {
            $quotedField = $this->quoteIdentifier($field);

            foreach ($ops as $op => $value) {
                $paramName = ":p{$paramIdx}";

                switch ($op) {
                    case 'eq':
                        $conditions[] = "{$quotedField} = {$paramName}";
                        $params[$paramName] = $value;
                        $paramIdx++;
                        break;
                    case 'neq':
                        $conditions[] = "{$quotedField} != {$paramName}";
                        $params[$paramName] = $value;
                        $paramIdx++;
                        break;
                    case 'gt':
                        $conditions[] = "{$quotedField} > {$paramName}";
                        $params[$paramName] = $value;
                        $paramIdx++;
                        break;
                    case 'gte':
                        $conditions[] = "{$quotedField} >= {$paramName}";
                        $params[$paramName] = $value;
                        $paramIdx++;
                        break;
                    case 'lt':
                        $conditions[] = "{$quotedField} < {$paramName}";
                        $params[$paramName] = $value;
                        $paramIdx++;
                        break;
                    case 'lte':
                        $conditions[] = "{$quotedField} <= {$paramName}";
                        $params[$paramName] = $value;
                        $paramIdx++;
                        break;
                    case 'in':
                        if (!is_array($value) || empty($value)) {
                            break;
                        }
                        $inParams = [];
                        foreach ($value as $v) {
                            $pName = ":p{$paramIdx}";
                            $inParams[] = $pName;
                            $params[$pName] = $v;
                            $paramIdx++;
                        }
                        $conditions[] = "{$quotedField} IN (" . implode(', ', $inParams) . ")";
                        break;
                    case 'like':
                        $conditions[] = "{$quotedField} LIKE {$paramName}";
                        $params[$paramName] = $value;
                        $paramIdx++;
                        break;
                    case 'is_null':
                        if ($value) {
                            $conditions[] = "{$quotedField} IS NULL";
                        } else {
                            $conditions[] = "{$quotedField} IS NOT NULL";
                        }
                        break;
                }
            }
        }

        return implode(' AND ', $conditions);
    }

    private function getPdo(string $dbName): \PDO
    {
        if (isset($this->pdoCache[$dbName])) {
            return $this->pdoCache[$dbName];
        }

        $conn = $this->connections[$dbName];

        if (empty($conn['password'])) {
            throw new \PDOException(
                "No password configured (check env file and password_env setting)"
            );
        }

        $pdo = new \PDO($conn['dsn'], $conn['user'], $conn['password'], [
            \PDO::ATTR_ERRMODE => \PDO::ERRMODE_EXCEPTION,
            \PDO::ATTR_DEFAULT_FETCH_MODE => \PDO::FETCH_ASSOC,
            \PDO::ATTR_EMULATE_PREPARES => false,
        ]);

        $pdo->exec("SET default_transaction_read_only = on");

        $this->pdoCache[$dbName] = $pdo;

        return $pdo;
    }

    private function quoteIdentifier(string $name): string
    {
        if (!preg_match('/^[a-zA-Z_][a-zA-Z0-9_]*$/', $name)) {
            throw new \InvalidArgumentException("Invalid identifier: {$name}");
        }
        return '"' . $name . '"';
    }

    private function formatDbError(\PDOException $e): string
    {
        $msg = $e->getMessage();
        if (str_contains($msg, 'password authentication failed') || str_contains($msg, 'No password configured')) {
            return "Database authentication failed (check password in env file)";
        }
        if (str_contains($msg, 'Connection refused') || str_contains($msg, 'could not connect')) {
            return "Database connection refused (check host/port)";
        }
        if (str_contains($msg, 'column') && str_contains($msg, 'does not exist')) {
            if (preg_match('/column "([^"]+)" does not exist/', $msg, $m)) {
                return "Column does not exist: {$m[1]} (check allowed_fields in config)";
            }
            return "Column does not exist (check allowed_fields in config)";
        }
        if (str_contains($msg, 'does not exist')) {
            return "Database or table does not exist";
        }
        if (str_contains($msg, 'permission denied')) {
            return "Database permission denied (check GRANT SELECT)";
        }
        return "Database query failed";
    }

    private function ok(string $text): array
    {
        return ['content' => [['type' => 'text', 'text' => $text]]];
    }

    private function error(string $text): array
    {
        return ['content' => [['type' => 'text', 'text' => $text]], 'isError' => true];
    }
}
