<?php

declare(strict_types=1);

namespace ServerLens\Audit;

final class AuditLogger
{
    private $handle = null;

    public function __construct(
        private readonly string $path,
        private readonly bool $logParams = false,
    ) {}

    public function log(
        string $clientIp,
        string $tool,
        array $params,
        bool $success,
        int $durationMs,
    ): void {
        $entry = [
            'timestamp' => gmdate('Y-m-d\TH:i:s\Z'),
            'client_ip' => $clientIp,
            'tool' => $tool,
            'params_summary' => $this->summarizeParams($params),
            'result' => [
                'status' => $success ? 'ok' : 'error',
                'duration_ms' => $durationMs,
            ],
        ];

        $line = json_encode($entry, JSON_UNESCAPED_UNICODE) . "\n";
        $this->write($line);
    }

    private function summarizeParams(array $params): array
    {
        if ($this->logParams) {
            return $params;
        }

        $summary = [];

        foreach ($params as $key => $value) {
            if ($key === 'source' || $key === 'database' || $key === 'table' || $key === 'service' || $key === 'stack') {
                $summary[$key] = $value;
            } elseif ($key === 'fields' && is_array($value)) {
                $summary['fields_count'] = count($value);
            } elseif ($key === 'filters' && is_array($value)) {
                $summary['has_filters'] = !empty($value);
            } elseif ($key === 'limit') {
                $summary['limit'] = $value;
            } elseif ($key === 'lines') {
                $summary['lines'] = $value;
            } elseif ($key === 'query') {
                $summary['query_length'] = strlen((string) $value);
            }
        }

        return $summary;
    }

    private function write(string $data): void
    {
        if ($this->handle === null) {
            $dir = dirname($this->path);
            if (!is_dir($dir)) {
                @mkdir($dir, 0750, true);
            }

            $this->handle = @fopen($this->path, 'a');
            if ($this->handle === false) {
                $this->handle = null;
                fwrite(STDERR, "[ServerLens] Cannot open audit log: {$this->path}\n");
                return;
            }
        }

        fwrite($this->handle, $data);
        fflush($this->handle);
    }

    public function __destruct()
    {
        if ($this->handle !== null) {
            fclose($this->handle);
        }
    }
}
