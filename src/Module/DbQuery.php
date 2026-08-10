<?php

declare(strict_types=1);

namespace ServerLens\Module;

use ServerLens\Config;
use ServerLens\Mcp\Tool;

final class DbQuery implements ModuleInterface
{
    private const VALID_OPS = [
        'eq', 'neq', 'gt', 'gte', 'lt', 'lte', 'in', 'not_in',
        'like', 'contains', 'between', 'is_null', 'array_contains',
    ];

    private const VALID_AGG_FNS = ['count', 'min', 'max', 'avg', 'sum'];

    /**
     * Read-only queries must never hold a connection hostage: a filter on an
     * unindexed column of a large table gets cut off instead of blocking the
     * whole MCP server.
     */
    private const STATEMENT_TIMEOUT_MS = 30000;

    /** @var array<string, array{driver: string, dsn: string, user: string, password: string, tables: array}> */
    private array $connections = [];

    /** @var array<string, \PDO> */
    private array $pdoCache = [];

    public function __construct(Config $config)
    {
        foreach ($config->getDatabaseConnections() as $conn) {
            $passwordEnv = $conn['password_env'] ?? '';
            $password = $passwordEnv ? (getenv($passwordEnv) ?: '') : '';

            $driver = $conn['driver'] ?? 'postgresql';
            $host = $conn['host'] ?? 'localhost';
            $port = $conn['port'] ?? ($driver === 'mysql' ? 3306 : 5432);
            $database = $conn['database'] ?? '';
            $user = $conn['user'] ?? '';

            if ($driver === 'mysql') {
                $dsn = "mysql:host={$host};port={$port};dbname={$database};charset=utf8mb4";
            } else {
                $dsn = "pgsql:host={$host};port={$port};dbname={$database}";
            }

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
                'driver' => $driver,
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
                        'description' => 'Filter conditions: {field: {op: value}}. Ops: eq, neq, gt, gte, lt, lte, in, not_in, like, contains, between, is_null, array_contains',
                    ],
                    'order_by' => [
                        'type' => 'array',
                        'items' => ['type' => 'string'],
                        'description' => 'Order by fields. Prefix with - for DESC.',
                    ],
                    'limit' => ['type' => 'integer', 'description' => 'Max rows to return'],
                    'offset' => ['type' => 'integer', 'description' => 'Offset for pagination', 'default' => 0],
                    'format' => [
                        'type' => 'string',
                        'enum' => ['json', 'tsv'],
                        'description' => 'Output format. tsv is ~4x more compact for wide result sets.',
                        'default' => 'json',
                    ],
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
            new Tool('db_aggregate', 'Aggregate records grouped by fields (no raw SQL). Find duplicates: group_by + having_min_count=2', [
                'type' => 'object',
                'properties' => [
                    'database' => ['type' => 'string', 'description' => 'Database connection name'],
                    'table' => ['type' => 'string', 'description' => 'Table name'],
                    'group_by' => [
                        'type' => 'array',
                        'items' => ['type' => 'string'],
                        'description' => "Fields to group by (must be in allowed_fields). May be empty when 'aggregates' is given — then the whole table is one group.",
                    ],
                    'aggregate' => [
                        'type' => 'string',
                        'description' => 'Single aggregate function; ignored when "aggregates" is given',
                        'enum' => ['count'],
                        'default' => 'count',
                    ],
                    'aggregates' => [
                        'type' => 'array',
                        'items' => ['type' => 'object'],
                        'description' => 'Several aggregates at once: [{fn: count|min|max|avg|sum, field?, alias?}]. count without a field means COUNT(*).',
                    ],
                    'filters' => [
                        'type' => 'object',
                        'description' => 'Filter conditions: {field: {op: value}}. Same operators as db_query',
                    ],
                    'having_min_count' => [
                        'type' => 'integer',
                        'description' => 'Only return groups with count >= X (optional)',
                    ],
                    'order' => [
                        'type' => 'string',
                        'description' => 'Sort by group count',
                        'enum' => ['count_desc', 'count_asc'],
                        'default' => 'count_desc',
                    ],
                    'order_by' => [
                        'type' => 'array',
                        'items' => ['type' => 'string'],
                        'description' => "Sort by group field or aggregate alias, prefix with - for DESC. Takes precedence over 'order'.",
                    ],
                    'limit' => ['type' => 'integer', 'description' => 'Max groups to return (capped at table max_rows)'],
                    'format' => [
                        'type' => 'string',
                        'enum' => ['json', 'tsv'],
                        'description' => 'tsv is ~4x more compact',
                        'default' => 'json',
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
            'db_aggregate' => $this->aggregate($arguments),
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
                'driver' => $conn['driver'],
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
        $requestedDb = $args['database'] ?? '';
        $dbName = $this->resolveDbName($requestedDb);
        if ($dbName === null) {
            return $this->dbNotFoundError($requestedDb);
        }
        $tableName = $args['table'] ?? '';

        $tableConfig = $this->connections[$dbName]['tables'][$tableName] ?? null;
        if ($tableConfig === null) {
            return $this->tableNotFoundError($dbName, $tableName);
        }

        $info = [
            'database' => $dbName,
            'table' => $tableName,
            'allowed_fields' => $tableConfig['allowed_fields'],
            'denied_fields' => $tableConfig['denied_fields'],
            'max_rows' => $tableConfig['max_rows'],
            // Effective sets, not the raw config keys: a readable column is
            // filterable and orderable by default, and config only extends that.
            'filterable_fields' => $this->resolveFilterable($tableConfig),
            'orderable_fields' => $this->resolveOrderable($tableConfig),
            'allowed_filters_config' => $tableConfig['allowed_filters'],
            'allowed_order_by_config' => $tableConfig['allowed_order_by'],
        ];

        return $this->ok(json_encode($info, JSON_PRETTY_PRINT | JSON_UNESCAPED_UNICODE));
    }

    private function query(array $args): array
    {
        $requestedDb = $args['database'] ?? '';
        $dbName = $this->resolveDbName($requestedDb);
        if ($dbName === null) {
            return $this->dbNotFoundError($requestedDb);
        }
        $tableName = $args['table'] ?? '';
        $fields = $args['fields'] ?? null;
        $filters = $args['filters'] ?? [];
        $orderBy = $args['order_by'] ?? [];
        $limit = (int) ($args['limit'] ?? 100);
        $offset = (int) ($args['offset'] ?? 0);

        $tableConfig = $this->connections[$dbName]['tables'][$tableName] ?? null;
        if ($tableConfig === null) {
            return $this->tableNotFoundError($dbName, $tableName);
        }

        $allowedFields = $this->resolveAllowedFields($tableConfig);
        if ($fields === null) {
            $fields = $allowedFields;
        }

        $driver = $this->connections[$dbName]['driver'];
        $validation = $this->validateQueryParams($fields, $filters, $orderBy, $tableConfig, $driver);
        if ($validation !== null) {
            return $this->error($validation);
        }

        $limit = min($limit, $tableConfig['max_rows']);
        $offset = max(0, $offset);

        try {
            $pdo = $this->getPdo($dbName);

            $quotedFields = array_map(fn($f) => $this->quoteIdentifier($f, $driver), $fields);
            $quotedTable = $this->quoteIdentifier($tableName, $driver);

            $sql = "SELECT " . implode(', ', $quotedFields) . " FROM {$quotedTable}";
            $params = [];

            $whereClause = $this->buildWhereClause($filters, $params, $driver);
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
                    $orderParts[] = $this->quoteIdentifier($field, $driver) . " {$dir}";
                }
                $sql .= " ORDER BY " . implode(', ', $orderParts);
            }

            $sql .= " LIMIT :_limit OFFSET :_offset";
            $params[':_limit'] = $limit;
            $params[':_offset'] = $offset;

            $stmt = $pdo->prepare($sql);
            $stmt->execute($params);
            $rows = $stmt->fetchAll(\PDO::FETCH_ASSOC);

            if (self::wantsTsv($args)) {
                $header = "# database={$dbName} table={$tableName} rows=" . count($rows)
                    . " offset={$offset} limit={$limit}";
                return $this->ok(self::toTsv($rows, $fields, $header));
            }

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
        $requestedDb = $args['database'] ?? '';
        $dbName = $this->resolveDbName($requestedDb);
        if ($dbName === null) {
            return $this->dbNotFoundError($requestedDb);
        }
        $tableName = $args['table'] ?? '';
        $filters = $args['filters'] ?? [];

        $tableConfig = $this->connections[$dbName]['tables'][$tableName] ?? null;
        if ($tableConfig === null) {
            return $this->tableNotFoundError($dbName, $tableName);
        }

        $validation = $this->validateFilters($filters, $tableConfig);
        if ($validation !== null) {
            return $this->error($validation);
        }

        try {
            $pdo = $this->getPdo($dbName);
            $driver = $this->connections[$dbName]['driver'];
            $quotedTable = $this->quoteIdentifier($tableName, $driver);
            $params = [];

            $sql = "SELECT COUNT(*) as count FROM {$quotedTable}";
            $whereClause = $this->buildWhereClause($filters, $params, $driver);
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

    private function aggregate(array $args): array
    {
        $requestedDb = $args['database'] ?? '';
        $dbName = $this->resolveDbName($requestedDb);
        if ($dbName === null) {
            return $this->dbNotFoundError($requestedDb);
        }
        $tableName = $args['table'] ?? '';
        $groupBy = $args['group_by'] ?? [];
        $aggregateFn = $args['aggregate'] ?? 'count';
        $aggregates = $args['aggregates'] ?? [];
        $filters = $args['filters'] ?? [];
        $havingMinCount = $args['having_min_count'] ?? null;
        $order = $args['order'] ?? 'count_desc';
        $orderBy = $args['order_by'] ?? [];
        $limit = (int) ($args['limit'] ?? 100);

        $tableConfig = $this->connections[$dbName]['tables'][$tableName] ?? null;
        if ($tableConfig === null) {
            return $this->tableNotFoundError($dbName, $tableName);
        }

        $driver = $this->connections[$dbName]['driver'];
        $validation = $this->validateAggregateParams(
            $groupBy,
            $aggregateFn,
            $filters,
            $havingMinCount,
            $order,
            $tableConfig,
            $aggregates,
            $orderBy,
            $driver,
        );
        if ($validation !== null) {
            return $this->error($validation);
        }

        $havingMinCount = $havingMinCount !== null ? (int) $havingMinCount : null;
        [$selectExtra, $aliases] = $this->buildAggregateSelects($aggregates, $driver);

        try {
            $pdo = $this->getPdo($dbName);
            $params = [];

            $sql = $this->buildAggregateSql(
                $tableName,
                $groupBy,
                $filters,
                $havingMinCount,
                $order,
                $limit,
                $tableConfig['max_rows'],
                $driver,
                $params,
                $selectExtra,
                $orderBy,
            );

            $stmt = $pdo->prepare($sql);
            $stmt->execute($params);
            $rows = $stmt->fetchAll(\PDO::FETCH_ASSOC);

            if (self::wantsTsv($args)) {
                $columns = array_merge($groupBy, $aliases ?: ['count']);
                $header = "# database={$dbName} table={$tableName} groups=" . count($rows);
                return $this->ok(self::toTsv($rows, $columns, $header));
            }

            $result = [
                'database' => $dbName,
                'table' => $tableName,
                'group_by' => $groupBy,
                'aggregate' => $aggregates ?: $aggregateFn,
                'groups_returned' => count($rows),
                'data' => $rows,
            ];

            return $this->ok(json_encode($result, JSON_PRETTY_PRINT | JSON_UNESCAPED_UNICODE));
        } catch (\PDOException $e) {
            fwrite(STDERR, "[ServerLens] DB error: {$e->getMessage()}\n");
            return $this->error($this->formatDbError($e));
        }
    }

    private function stats(array $args): array
    {
        $requestedDb = $args['database'] ?? '';
        $dbName = $this->resolveDbName($requestedDb);
        if ($dbName === null) {
            return $this->dbNotFoundError($requestedDb);
        }
        $tableName = $args['table'] ?? '';
        $field = $args['field'] ?? '';

        $tableConfig = $this->connections[$dbName]['tables'][$tableName] ?? null;
        if ($tableConfig === null) {
            return $this->tableNotFoundError($dbName, $tableName);
        }

        $allowedFields = $this->resolveAllowedFields($tableConfig);
        if (!in_array($field, $allowedFields, true)) {
            return $this->error("Field not allowed: {$field}");
        }

        try {
            $pdo = $this->getPdo($dbName);
            $driver = $this->connections[$dbName]['driver'];
            $quotedTable = $this->quoteIdentifier($tableName, $driver);
            $quotedField = $this->quoteIdentifier($field, $driver);

            if ($driver === 'mysql') {
                $sql = "SELECT
                    COUNT({$quotedField}) as count,
                    MIN({$quotedField}) as min,
                    MAX({$quotedField}) as max,
                    AVG({$quotedField}) as avg
                    FROM {$quotedTable}";
            } else {
                $sql = "SELECT
                    COUNT({$quotedField}) as count,
                    MIN({$quotedField}) as min,
                    MAX({$quotedField}) as max,
                    AVG({$quotedField}::numeric) as avg
                    FROM {$quotedTable}";
            }

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

    private function resolveDbName(string $dbName): ?string
    {
        if ($dbName !== '' && isset($this->connections[$dbName])) {
            return $dbName;
        }

        if (count($this->connections) === 1) {
            return array_key_first($this->connections);
        }

        return null;
    }

    private function dbNotFoundError(string $requested): array
    {
        $available = implode(', ', array_keys($this->connections));
        return $this->error("Unknown database connection: '{$requested}'. Available: [{$available}]");
    }

    private function tableNotFoundError(string $dbName, string $tableName): array
    {
        $count = count($this->connections[$dbName]['tables']);
        return $this->error("Unknown table: '{$tableName}' in database '{$dbName}'. Total tables: {$count}. Use db_list to see all.");
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

    /**
     * Fields a filter may reference.
     *
     * A readable column is a filterable column: if the model may already SELECT
     * the value, filtering by it discloses nothing new, and forcing operators to
     * maintain a second parallel whitelist only means diagnostics stall on a
     * config edit. allowed_filters therefore EXTENDS the set — it is for columns
     * you want filterable but not visible — and denied_fields still wins.
     *
     * @return string[]
     */
    private function resolveFilterable(array $tableConfig): array
    {
        $allowed = $this->resolveAllowedFields($tableConfig);
        if ($allowed === ['*']) {
            return ['*'];
        }

        $denied = $tableConfig['denied_fields'];
        $extra = array_diff($tableConfig['allowed_filters'], $allowed, $denied);

        return array_values(array_merge($allowed, $extra));
    }

    /**
     * Fields ORDER BY may reference — same reasoning as resolveFilterable().
     *
     * @return string[]
     */
    private function resolveOrderable(array $tableConfig): array
    {
        $allowed = $this->resolveAllowedFields($tableConfig);
        if ($allowed === ['*']) {
            return ['*'];
        }

        $denied = $tableConfig['denied_fields'];
        $extra = array_diff($tableConfig['allowed_order_by'], $allowed, $denied);

        return array_values(array_merge($allowed, $extra));
    }

    private function validateQueryParams(array $fields, array $filters, array $orderBy, array $tableConfig, string $driver = 'postgresql'): ?string
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

        $validationError = $this->validateFilters($filters, $tableConfig, $driver);
        if ($validationError !== null) {
            return $validationError;
        }

        $orderable = $this->resolveOrderable($tableConfig);
        if ($orderable !== ['*']) {
            foreach ($orderBy as $ob) {
                $field = ltrim($ob, '-');
                if (!in_array($field, $orderable, true)) {
                    $allowed = implode(', ', $orderable);
                    return "Order by field not allowed: {$field}. Allowed: [{$allowed}]";
                }
            }
        }

        return null;
    }

    private function validateFilters(array $filters, array $tableConfig, string $driver = 'postgresql'): ?string
    {
        $filterable = $this->resolveFilterable($tableConfig);

        foreach ($filters as $field => $conditions) {
            if ($filterable !== ['*'] && !in_array($field, $filterable, true)) {
                $allowed = implode(', ', $filterable);
                return "Filter on field not allowed: {$field}. Allowed: [{$allowed}]";
            }

            if (!is_array($conditions)) {
                return "Invalid filter format for field: {$field}";
            }

            foreach ($conditions as $op => $value) {
                if (!in_array($op, self::VALID_OPS, true)) {
                    $valid = implode(', ', self::VALID_OPS);
                    return "Invalid filter operator: {$op}. Valid: [{$valid}]";
                }

                if ($op === 'in' || $op === 'not_in') {
                    if (!is_array($value)) {
                        return "Operator '{$op}' expects a list of values";
                    }
                    if (count($value) > 50) {
                        return "Operator '{$op}' is limited to 50 values";
                    }
                    continue;
                }

                if ($op === 'between') {
                    if (!is_array($value) || count($value) !== 2) {
                        return "Operator 'between' expects a list of exactly two values";
                    }
                    continue;
                }

                if ($op === 'array_contains') {
                    if ($driver === 'mysql') {
                        return "Operator 'array_contains' is PostgreSQL-only";
                    }
                    if (!is_scalar($value)) {
                        return "Filter values must be scalar";
                    }
                    continue;
                }

                if ($op !== 'is_null' && !is_scalar($value)) {
                    return "Filter values must be scalar";
                }
            }
        }

        return null;
    }

    private function validateAggregateParams(
        mixed $groupBy,
        mixed $aggregateFn,
        array $filters,
        mixed $havingMinCount,
        mixed $order,
        array $tableConfig,
        mixed $aggregates = null,
        mixed $orderBy = null,
        string $driver = 'postgresql',
    ): ?string {
        $aggregates = $aggregates ?: [];
        $orderBy = $orderBy ?: [];

        if (!is_array($aggregates)) {
            return "aggregates must be an array of {fn, field?, alias?} objects";
        }

        // Without an explicit aggregates spec the tool computes COUNT(*) per
        // group, and a group-less COUNT(*) is just db_count — so keep requiring
        // group_by in that case.
        if (!is_array($groupBy) || (empty($groupBy) && empty($aggregates))) {
            return "group_by must be a non-empty array of fields";
        }

        $allowedFields = $this->resolveAllowedFields($tableConfig);
        $isWildcard = $allowedFields === ['*'];
        $deniedFields = $tableConfig['denied_fields'];

        foreach ($groupBy as $field) {
            if (!is_string($field) || !preg_match('/^[a-zA-Z_][a-zA-Z0-9_]*$/', $field)) {
                $printable = is_scalar($field) ? (string) $field : gettype($field);
                return "Invalid group_by field: {$printable}";
            }
            if (!$isWildcard && !in_array($field, $allowedFields, true)) {
                return "Group by field not allowed: {$field}";
            }
            if (in_array($field, $deniedFields, true)) {
                return "Access denied to field: {$field}";
            }
        }

        if (empty($aggregates) && $aggregateFn !== 'count') {
            $printable = is_scalar($aggregateFn) ? (string) $aggregateFn : gettype($aggregateFn);
            return "Invalid aggregate function: {$printable}. Supported: count";
        }

        $aliases = $groupBy;
        foreach ($aggregates as $agg) {
            if (!is_array($agg)) {
                return "Each aggregate must be an object: {fn, field?, alias?}";
            }

            $fn = strtolower((string) ($agg['fn'] ?? ''));
            if (!in_array($fn, self::VALID_AGG_FNS, true)) {
                $valid = implode(', ', self::VALID_AGG_FNS);
                return "Invalid aggregate function: {$fn}. Valid: [{$valid}]";
            }

            $field = $agg['field'] ?? null;
            if ($field === null) {
                if ($fn !== 'count') {
                    return "Aggregate '{$fn}' requires a field";
                }
                $defaultAlias = 'count';
            } else {
                if (!is_string($field) || !preg_match('/^[a-zA-Z_][a-zA-Z0-9_]*$/', $field)) {
                    $printable = is_scalar($field) ? (string) $field : gettype($field);
                    return "Invalid aggregate field: {$printable}";
                }
                if (!$isWildcard && !in_array($field, $allowedFields, true)) {
                    return "Aggregate field not allowed: {$field}";
                }
                if (in_array($field, $deniedFields, true)) {
                    return "Access denied to field: {$field}";
                }
                $defaultAlias = "{$fn}_{$field}";
            }

            $alias = (string) ($agg['alias'] ?? '');
            if ($alias === '') {
                $alias = $defaultAlias;
            }
            if (!preg_match('/^[a-zA-Z_][a-zA-Z0-9_]*$/', $alias)) {
                return "Invalid aggregate alias: {$alias}";
            }
            if (in_array($alias, $aliases, true)) {
                return "Duplicate output name: {$alias}";
            }
            $aliases[] = $alias;
        }

        if (empty($aggregates)) {
            $aliases[] = 'count';
        }

        if (!is_array($orderBy)) {
            return "order_by must be an array";
        }
        foreach ($orderBy as $ob) {
            if (!is_string($ob) || !in_array(ltrim($ob, '-'), $aliases, true)) {
                $printable = is_scalar($ob) ? (string) $ob : gettype($ob);
                $available = implode(', ', $aliases);
                return "Order by must reference a group field or aggregate alias: {$printable}. Available: [{$available}]";
            }
        }

        if ($havingMinCount !== null && (!is_numeric($havingMinCount) || (int) $havingMinCount < 1)) {
            return "having_min_count must be a positive integer";
        }

        if (empty($orderBy) && !in_array($order, ['count_desc', 'count_asc'], true)) {
            $printable = is_scalar($order) ? (string) $order : gettype($order);
            return "Invalid order: {$printable}. Allowed: [count_desc, count_asc]";
        }

        return $this->validateFilters($filters, $tableConfig, $driver);
    }

    /**
     * Turn the aggregates spec into SELECT expressions plus their aliases.
     * Assumes validateAggregateParams() already accepted the spec.
     *
     * @return array{0: string[], 1: string[]}
     */
    private function buildAggregateSelects(array $aggregates, string $driver): array
    {
        $selectExtra = [];
        $aliases = [];

        foreach ($aggregates as $agg) {
            $fn = strtolower((string) ($agg['fn'] ?? 'count'));
            $field = $agg['field'] ?? null;

            if ($field === null) {
                $expr = 'COUNT(*)';
                $defaultAlias = 'count';
            } else {
                $quoted = $this->quoteIdentifier((string) $field, $driver);
                // Postgres refuses AVG on integer-like columns without a cast.
                $expr = ($fn === 'avg' && $driver !== 'mysql')
                    ? "AVG({$quoted}::numeric)"
                    : strtoupper($fn) . "({$quoted})";
                $defaultAlias = "{$fn}_{$field}";
            }

            $alias = (string) ($agg['alias'] ?? '');
            if ($alias === '') {
                $alias = $defaultAlias;
            }

            $aliases[] = $alias;
            $selectExtra[] = $expr . ' AS ' . $this->quoteIdentifier($alias, $driver);
        }

        return [$selectExtra, $aliases];
    }

    /**
     * @param string[] $groupBy
     */
    private function buildAggregateSql(
        string $tableName,
        array $groupBy,
        array $filters,
        ?int $havingMinCount,
        string $order,
        int $limit,
        int $maxRows,
        string $driver,
        array &$params,
        ?array $selectExtra = null,
        ?array $orderBy = null,
    ): string {
        $quotedTable = $this->quoteIdentifier($tableName, $driver);
        $quotedGroupBy = array_map(fn($f) => $this->quoteIdentifier($f, $driver), $groupBy);
        $groupByClause = implode(', ', $quotedGroupBy);

        $selectParts = $groupByClause !== '' ? [$groupByClause] : [];
        foreach ($selectExtra ?: ['COUNT(*) AS count'] as $part) {
            $selectParts[] = $part;
        }
        $sql = 'SELECT ' . implode(', ', $selectParts) . " FROM {$quotedTable}";

        $whereClause = $this->buildWhereClause($filters, $params, $driver);
        if ($whereClause) {
            $sql .= " WHERE {$whereClause}";
        }

        if ($groupByClause !== '') {
            $sql .= " GROUP BY {$groupByClause}";
        }

        if ($havingMinCount !== null) {
            $sql .= " HAVING COUNT(*) >= :_having";
            $params[':_having'] = $havingMinCount;
        }

        if (!empty($orderBy)) {
            $orderParts = [];
            foreach ($orderBy as $ob) {
                $dir = str_starts_with($ob, '-') ? 'DESC' : 'ASC';
                $orderParts[] = $this->quoteIdentifier(ltrim($ob, '-'), $driver) . " {$dir}";
            }
            $sql .= ' ORDER BY ' . implode(', ', $orderParts);
        } elseif (empty($selectExtra)) {
            $direction = $order === 'count_asc' ? 'ASC' : 'DESC';
            $sql .= " ORDER BY COUNT(*) {$direction}";
        }

        $sql .= " LIMIT :_limit";
        $params[':_limit'] = min(max($limit, 1), $maxRows);

        return $sql;
    }

    private function buildWhereClause(array $filters, array &$params, string $driver = 'postgresql'): string
    {
        $conditions = [];
        $paramIdx = 0;

        foreach ($filters as $field => $ops) {
            $quotedField = $this->quoteIdentifier($field, $driver);

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
                    case 'not_in':
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
                        $keyword = $op === 'in' ? 'IN' : 'NOT IN';
                        $conditions[] = "{$quotedField} {$keyword} (" . implode(', ', $inParams) . ")";
                        break;
                    case 'like':
                        $conditions[] = "{$quotedField} LIKE {$paramName}";
                        $params[$paramName] = $value;
                        $paramIdx++;
                        break;
                    case 'contains':
                        $conditions[] = "{$quotedField} LIKE {$paramName} ESCAPE '\\'";
                        $params[$paramName] = '%' . self::escapeLike((string) $value) . '%';
                        $paramIdx++;
                        break;
                    case 'between':
                        $lowName = ":p{$paramIdx}";
                        $paramIdx++;
                        $highName = ":p{$paramIdx}";
                        $paramIdx++;
                        $conditions[] = "{$quotedField} BETWEEN {$lowName} AND {$highName}";
                        $params[$lowName] = $value[0];
                        $params[$highName] = $value[1];
                        break;
                    case 'array_contains':
                        // PostgreSQL array column: value = ANY(col). Rejected for
                        // MySQL during validation.
                        $conditions[] = "{$paramName} = ANY({$quotedField})";
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

        if ($conn['driver'] === 'mysql') {
            $pdo->exec("SET SESSION TRANSACTION READ ONLY");
            $pdo->exec('SET SESSION MAX_EXECUTION_TIME = ' . self::STATEMENT_TIMEOUT_MS);
        } else {
            $pdo->exec("SET default_transaction_read_only = on");
            $pdo->exec('SET statement_timeout = ' . self::STATEMENT_TIMEOUT_MS);
        }

        $this->pdoCache[$dbName] = $pdo;

        return $pdo;
    }

    /** Neutralise LIKE wildcards so 'contains' means a literal substring. */
    private static function escapeLike(string $value): string
    {
        return str_replace(['\\', '%', '_'], ['\\\\', '\\%', '\\_'], $value);
    }

    private static function wantsTsv(array $args): bool
    {
        return strtolower((string) ($args['format'] ?? 'json')) === 'tsv';
    }

    /**
     * Compact output: one header line, one line per row, tabs between values.
     *
     * Roughly four times smaller than pretty-printed JSON on wide result sets,
     * which is what decides whether a result fits in the caller's context.
     *
     * @param array<int, array<string, mixed>> $rows
     * @param string[] $columns
     */
    private static function toTsv(array $rows, array $columns, string $header): string
    {
        if (!empty($rows) && $columns === ['*']) {
            $columns = array_keys($rows[0]);
        }

        $lines = [$header, implode("\t", $columns)];
        foreach ($rows as $row) {
            $cells = [];
            foreach ($columns as $column) {
                $value = $row[$column] ?? null;
                $cells[] = $value === null
                    ? ''
                    : str_replace(["\t", "\n", "\r"], ' ', (string) $value);
            }
            $lines[] = implode("\t", $cells);
        }

        return implode("\n", $lines);
    }

    private function quoteIdentifier(string $name, string $driver = 'postgresql'): string
    {
        if (!preg_match('/^[a-zA-Z_][a-zA-Z0-9_]*$/', $name)) {
            throw new \InvalidArgumentException("Invalid identifier: {$name}");
        }
        if ($driver === 'mysql') {
            return '`' . $name . '`';
        }
        return '"' . $name . '"';
    }

    private function formatDbError(\PDOException $e): string
    {
        $msg = $e->getMessage();
        if (str_contains($msg, 'password authentication failed') || str_contains($msg, 'No password configured') || str_contains($msg, 'Access denied')) {
            return "Database authentication failed (check password in env file)";
        }
        if (str_contains($msg, 'Connection refused') || str_contains($msg, 'could not connect') || str_contains($msg, "Can't connect")) {
            return "Database connection refused (check host/port)";
        }
        if (str_contains($msg, 'Unknown column')) {
            if (preg_match("/Unknown column '([^']+)'/", $msg, $m)) {
                return "Column does not exist: {$m[1]} (check allowed_fields in config)";
            }
            return "Column does not exist (check allowed_fields in config)";
        }
        if (str_contains($msg, 'column') && str_contains($msg, 'does not exist')) {
            if (preg_match('/column "([^"]+)" does not exist/', $msg, $m)) {
                return "Column does not exist: {$m[1]} (check allowed_fields in config)";
            }
            return "Column does not exist (check allowed_fields in config)";
        }
        if (str_contains($msg, 'does not exist') || str_contains($msg, "doesn't exist")) {
            return "Database or table does not exist";
        }
        if (str_contains($msg, 'permission denied') || str_contains($msg, 'command denied')) {
            return "Database permission denied (check GRANT SELECT)";
        }
        if (str_contains($msg, 'statement timeout') || str_contains($msg, 'maximum statement execution time')) {
            $seconds = intdiv(self::STATEMENT_TIMEOUT_MS, 1000);
            return "Query exceeded the {$seconds}s read-only timeout — narrow the filters or group over fewer rows";
        }
        if (str_contains($msg, 'must appear in the GROUP BY') || str_contains($msg, 'not in GROUP BY')) {
            return "Aggregate query is invalid: every selected field must be grouped or aggregated";
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
