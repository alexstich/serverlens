<?php

declare(strict_types=1);

namespace ServerLens;

use ServerLens\Audit\AuditLogger;
use ServerLens\Auth\RateLimiter;
use ServerLens\Auth\TokenAuth;
use ServerLens\Mcp\Server;
use ServerLens\Module\ConfigReader;
use ServerLens\Module\DbQuery;
use ServerLens\Module\LogReader;
use ServerLens\Module\SystemInfo;
use ServerLens\Transport\SseTransport;
use ServerLens\Transport\StdioTransport;
use ServerLens\Transport\TransportInterface;

final class Application
{
    private Config $config;
    private Server $mcpServer;
    private TransportInterface $transport;

    public function __construct(string $configPath)
    {
        $this->config = Config::load($configPath);
        $this->boot();
    }

    public function run(): void
    {
        fwrite(STDERR, "[ServerLens] Starting server...\n");
        fwrite(STDERR, "[ServerLens] Transport: {$this->config->getTransport()}\n");

        $this->transport->onMessage(function (array $message, string $clientIp): ?array {
            return $this->mcpServer->handleMessage($message, $clientIp);
        });

        $this->transport->start();
    }

    private function boot(): void
    {
        $audit = null;
        if ($this->config->isAuditEnabled()) {
            $audit = new AuditLogger(
                $this->config->getAuditPath(),
                $this->config->shouldLogParams(),
            );
        }

        $rateLimiter = new RateLimiter(
            $this->config->getRequestsPerMinute(),
            $this->config->getMaxConcurrent(),
        );

        $this->mcpServer = new Server($audit, $rateLimiter);

        $this->registerModules();
        $this->createTransport();
    }

    private function registerModules(): void
    {
        if (!empty($this->config->getLogSources())) {
            $this->mcpServer->registerModule(new LogReader($this->config));
            fwrite(STDERR, "[ServerLens] Module loaded: LogReader\n");
        }

        if (!empty($this->config->getConfigSources())) {
            $this->mcpServer->registerModule(new ConfigReader($this->config));
            fwrite(STDERR, "[ServerLens] Module loaded: ConfigReader\n");
        }

        if (!empty($this->config->getDatabaseConnections())) {
            $this->mcpServer->registerModule(new DbQuery($this->config));
            fwrite(STDERR, "[ServerLens] Module loaded: DbQuery\n");
        }

        if ($this->config->isSystemEnabled()) {
            $this->mcpServer->registerModule(new SystemInfo($this->config));
            fwrite(STDERR, "[ServerLens] Module loaded: SystemInfo\n");
        }
    }

    private function createTransport(): void
    {
        $transportType = $this->config->getTransport();

        if ($transportType === 'sse') {
            $auth = new TokenAuth($this->config);
            $this->transport = new SseTransport(
                $this->config->getServerHost(),
                $this->config->getServerPort(),
                $auth,
            );
        } else {
            $this->transport = new StdioTransport();
        }
    }
}
