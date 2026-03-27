<?php

declare(strict_types=1);

namespace ServerLens\Module;

use ServerLens\Config;
use ServerLens\Mcp\Tool;

final class SystemInfo implements ModuleInterface
{
    private bool $enabled;
    /** @var string[] */
    private array $allowedServices;
    /** @var string[] */
    private array $allowedDockerStacks;

    public function __construct(Config $config)
    {
        $this->enabled = $config->isSystemEnabled();
        $this->allowedServices = $config->getAllowedServices();
        $this->allowedDockerStacks = $config->getAllowedDockerStacks();
    }

    public function getTools(): array
    {
        if (!$this->enabled) {
            return [];
        }

        return [
            new Tool('system_overview', 'Get CPU, RAM, disk usage, and uptime', [
                'type' => 'object',
                'properties' => new \stdClass(),
            ]),
            new Tool('system_services', 'Get status of allowed systemd services', [
                'type' => 'object',
                'properties' => [
                    'service' => [
                        'type' => 'string',
                        'description' => 'Specific service name (optional, shows all if omitted)',
                    ],
                ],
            ]),
            new Tool('system_docker', 'Get status of allowed Docker containers', [
                'type' => 'object',
                'properties' => [
                    'stack' => [
                        'type' => 'string',
                        'description' => 'Docker stack/compose name (optional)',
                    ],
                ],
            ]),
            new Tool('system_connections', 'Get active database and service connection counts', [
                'type' => 'object',
                'properties' => new \stdClass(),
            ]),
            new Tool('system_processes', 'Get top processes by CPU or memory usage (like htop)', [
                'type' => 'object',
                'properties' => [
                    'sort_by' => [
                        'type' => 'string',
                        'description' => 'Sort by "cpu" (default) or "memory"',
                        'enum' => ['cpu', 'memory'],
                    ],
                    'limit' => [
                        'type' => 'integer',
                        'description' => 'Number of processes to return (default 20, max 100)',
                    ],
                    'user' => [
                        'type' => 'string',
                        'description' => 'Filter by OS user (optional)',
                    ],
                    'filter' => [
                        'type' => 'string',
                        'description' => 'Filter by command name substring (optional)',
                    ],
                ],
            ]),
        ];
    }

    public function handleToolCall(string $name, array $arguments): array
    {
        if (!$this->enabled) {
            return $this->error("System module is disabled");
        }

        return match ($name) {
            'system_overview' => $this->overview(),
            'system_services' => $this->services($arguments),
            'system_docker' => $this->docker($arguments),
            'system_connections' => $this->connections(),
            'system_processes' => $this->processes($arguments),
            default => $this->error("Unknown tool: {$name}"),
        };
    }

    private function overview(): array
    {
        $info = [];

        $info['uptime'] = trim($this->exec('uptime -p') ?: $this->exec('uptime') ?: 'N/A');
        $info['load_average'] = trim($this->exec('cat /proc/loadavg') ?: 'N/A');

        $memInfo = $this->exec('free -h --si');
        if ($memInfo) {
            $info['memory'] = $memInfo;
        }

        $diskInfo = $this->exec('df -h --total 2>/dev/null | grep -E "^(/dev|total)"');
        if ($diskInfo) {
            $info['disk'] = $diskInfo;
        }

        $cpuCount = trim($this->exec('nproc') ?: 'N/A');
        $info['cpu_cores'] = $cpuCount;

        return $this->ok(json_encode($info, JSON_PRETTY_PRINT | JSON_UNESCAPED_UNICODE));
    }

    private function services(array $args): array
    {
        $specific = $args['service'] ?? null;

        if ($specific !== null) {
            if (!in_array($specific, $this->allowedServices, true)) {
                return $this->error("Service not in whitelist: {$specific}");
            }

            $status = $this->getServiceStatus($specific);
            return $this->ok(json_encode($status, JSON_PRETTY_PRINT));
        }

        $results = [];
        foreach ($this->allowedServices as $service) {
            $results[] = $this->getServiceStatus($service);
        }

        return $this->ok(json_encode($results, JSON_PRETTY_PRINT | JSON_UNESCAPED_UNICODE));
    }

    private function docker(array $args): array
    {
        $stack = $args['stack'] ?? null;

        if ($stack !== null && !in_array($stack, $this->allowedDockerStacks, true)) {
            return $this->error("Docker stack not in whitelist: {$stack}");
        }

        $output = $this->exec('docker ps --format "{{.Names}}\t{{.Status}}\t{{.Image}}\t{{.Ports}}" 2>/dev/null');
        if ($output === null || $output === '') {
            return $this->ok("Docker not available or no running containers");
        }

        $lines = explode("\n", trim($output));
        $containers = [];

        foreach ($lines as $line) {
            $parts = explode("\t", $line);
            if (count($parts) < 3) {
                continue;
            }

            $name = $parts[0];

            $matchesStack = false;
            $stacks = $stack !== null ? [$stack] : $this->allowedDockerStacks;
            foreach ($stacks as $s) {
                if (str_starts_with($name, $s . '-') || str_starts_with($name, $s . '_')) {
                    $matchesStack = true;
                    break;
                }
            }

            if (!$matchesStack) {
                continue;
            }

            $containers[] = [
                'name' => $name,
                'status' => $parts[1] ?? '',
                'image' => $parts[2] ?? '',
                'ports' => $parts[3] ?? '',
            ];
        }

        if (empty($containers)) {
            return $this->ok("No matching containers found");
        }

        return $this->ok(json_encode($containers, JSON_PRETTY_PRINT | JSON_UNESCAPED_UNICODE));
    }

    private function connections(): array
    {
        $result = [];

        $pgConns = $this->exec("psql -t -c \"SELECT count(*) FROM pg_stat_activity WHERE state = 'active'\" 2>/dev/null");
        if ($pgConns !== null) {
            $result['postgresql_active'] = (int) trim($pgConns);
        }

        $pgTotal = $this->exec("psql -t -c \"SELECT count(*) FROM pg_stat_activity\" 2>/dev/null");
        if ($pgTotal !== null) {
            $result['postgresql_total'] = (int) trim($pgTotal);
        }

        $rmqIncoming = $this->exec("ss -tn state established 'sport = :5672' 2>/dev/null | tail -n +2 | wc -l");
        if ($rmqIncoming !== null) {
            $result['rabbitmq_incoming'] = (int) trim($rmqIncoming);
        }

        $rmqOutgoing = $this->exec("ss -tn state established 'dport = :5672' 2>/dev/null | tail -n +2 | wc -l");
        if ($rmqOutgoing !== null) {
            $result['rabbitmq_outgoing'] = (int) trim($rmqOutgoing);
        }

        $result['rabbitmq_connections'] = ($result['rabbitmq_incoming'] ?? 0) + ($result['rabbitmq_outgoing'] ?? 0);

        $established = $this->exec("ss -tun state established 2>/dev/null | wc -l");
        if ($established !== null) {
            $result['tcp_established'] = max(0, (int) trim($established) - 1);
        }

        return $this->ok(json_encode($result, JSON_PRETTY_PRINT));
    }

    private function processes(array $args): array
    {
        $sortBy = $args['sort_by'] ?? 'cpu';
        $limit = min(max((int) ($args['limit'] ?? 20), 1), 100);
        $user = $args['user'] ?? null;
        $filter = $args['filter'] ?? null;

        $sortFlag = $sortBy === 'memory' ? '-%mem' : '-%cpu';

        $cmd = "ps aux --sort={$sortFlag} 2>/dev/null";
        $output = $this->exec($cmd);
        if ($output === null || $output === '') {
            return $this->error("Failed to execute ps command");
        }

        $lines = explode("\n", trim($output));
        if (count($lines) < 2) {
            return $this->ok("No processes found");
        }

        $header = preg_split('/\s+/', trim($lines[0]), 11);
        $processes = [];

        for ($i = 1, $count = count($lines); $i < $count; $i++) {
            $parts = preg_split('/\s+/', trim($lines[$i]), 11);
            if (count($parts) < 11) {
                continue;
            }

            $procUser = $parts[0];
            $command = $parts[10];

            if ($user !== null && $procUser !== $user) {
                continue;
            }
            if ($filter !== null && stripos($command, $filter) === false) {
                continue;
            }

            $processes[] = [
                'user' => $procUser,
                'pid' => (int) $parts[1],
                'cpu' => (float) $parts[2],
                'mem' => (float) $parts[3],
                'vsz_kb' => (int) $parts[4],
                'rss_kb' => (int) $parts[5],
                'stat' => $parts[7],
                'time' => $parts[9],
                'command' => $command,
            ];

            if (count($processes) >= $limit) {
                break;
            }
        }

        $result = [
            'sort_by' => $sortBy,
            'total_shown' => count($processes),
            'filters' => array_filter([
                'user' => $user,
                'command' => $filter,
            ]),
            'processes' => $processes,
        ];

        return $this->ok(json_encode($result, JSON_PRETTY_PRINT | JSON_UNESCAPED_UNICODE));
    }

    private function getServiceStatus(string $service): array
    {
        $escapedService = escapeshellarg($service);
        $isActive = trim($this->exec("systemctl is-active {$escapedService} 2>/dev/null") ?: 'unknown');
        $isEnabled = trim($this->exec("systemctl is-enabled {$escapedService} 2>/dev/null") ?: 'unknown');

        $memory = null;
        $mainPid = null;
        $showOutput = $this->exec("systemctl show {$escapedService} --property=MainPID,MemoryCurrent 2>/dev/null");
        if ($showOutput) {
            foreach (explode("\n", $showOutput) as $line) {
                if (str_starts_with($line, 'MainPID=')) {
                    $mainPid = (int) substr($line, 8);
                }
                if (str_starts_with($line, 'MemoryCurrent=')) {
                    $bytes = substr($line, 14);
                    if (is_numeric($bytes)) {
                        $memory = $this->formatBytes((int) $bytes);
                    }
                }
            }
        }

        return [
            'service' => $service,
            'active' => $isActive,
            'enabled' => $isEnabled,
            'pid' => $mainPid,
            'memory' => $memory,
        ];
    }

    private function exec(string $command): ?string
    {
        $output = @shell_exec($command);
        return is_string($output) ? $output : null;
    }

    private function formatBytes(int $bytes): string
    {
        $units = ['B', 'KB', 'MB', 'GB'];
        $i = 0;
        $size = (float) $bytes;
        while ($size >= 1024 && $i < count($units) - 1) {
            $size /= 1024;
            $i++;
        }
        return round($size, 1) . ' ' . $units[$i];
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
