<?php

declare(strict_types=1);

namespace Tests;

use PHPUnit\Framework\TestCase;
use ServerLens\Config;
use ServerLens\Module\JournalReader;

final class JournalReaderTest extends TestCase
{
    /** @var string[] */
    private array $executedCommands = [];

    private ?string $fakeOutput = '';

    private JournalReader $reader;

    protected function setUp(): void
    {
        $this->executedCommands = [];
        $this->fakeOutput = '';
        $this->reader = $this->makeReader();
    }

    public function testJournalUnitsListsWhitelist(): void
    {
        $result = $this->reader->handleToolCall('journal_units', []);
        $data = json_decode($result['content'][0]['text'], true);

        $this->assertSame(['nginx', 'postgresql', 'php8.2-fpm'], $data['allowed_units']);
        $this->assertEmpty($this->executedCommands);
    }

    public function testToolsExposedWhenEnabled(): void
    {
        $names = array_map(fn($t) => $t->name, $this->reader->getTools());

        $this->assertSame(['journal_units', 'journal_tail', 'journal_search'], $names);
    }

    public function testDisabledModuleHasNoToolsAndRejectsCalls(): void
    {
        $reader = $this->makeReader(enabled: false);

        $this->assertSame([], $reader->getTools());

        $result = $reader->handleToolCall('journal_tail', ['unit' => 'nginx']);
        $this->assertTrue($result['isError']);
        $this->assertEmpty($this->executedCommands);
    }

    public function testTailRejectsUnknownUnit(): void
    {
        $result = $this->reader->handleToolCall('journal_tail', ['unit' => 'sshd']);

        $this->assertTrue($result['isError']);
        $this->assertStringContainsString('not in whitelist', $result['content'][0]['text']);
        $this->assertEmpty($this->executedCommands);
    }

    public function testTailRejectsPartialUnitMatch(): void
    {
        foreach (['ngin', 'nginx2', 'nginx.service', ' nginx', ''] as $unit) {
            $result = $this->reader->handleToolCall('journal_tail', ['unit' => $unit]);
            $this->assertTrue($result['isError'], "Unit '{$unit}' must be rejected");
        }

        $this->assertEmpty($this->executedCommands);
    }

    public function testTailBuildsEscapedCommand(): void
    {
        $this->fakeOutput = "2026-07-18T10:00:01+0300 host nginx[1]: started\n";

        $result = $this->reader->handleToolCall('journal_tail', ['unit' => 'nginx', 'lines' => 5]);

        $this->assertArrayNotHasKey('isError', $result);
        $this->assertCount(1, $this->executedCommands);
        $this->assertSame(
            "journalctl -u 'nginx' -n 5 --no-pager -o short-iso 2>/dev/null",
            $this->executedCommands[0],
        );
        $this->assertStringContainsString('started', $result['content'][0]['text']);
    }

    public function testTailCapsLinesAtMax(): void
    {
        $this->fakeOutput = "line\n";

        $this->reader->handleToolCall('journal_tail', ['unit' => 'nginx', 'lines' => 99999]);

        $this->assertStringContainsString(' -n 500 ', $this->executedCommands[0]);
    }

    public function testTailFailureReturnsError(): void
    {
        $this->fakeOutput = null;

        $result = $this->reader->handleToolCall('journal_tail', ['unit' => 'nginx']);

        $this->assertTrue($result['isError']);
    }

    public function testSearchRejectsUnknownUnit(): void
    {
        $result = $this->reader->handleToolCall('journal_search', [
            'unit' => 'sshd',
            'query' => 'error',
        ]);

        $this->assertTrue($result['isError']);
        $this->assertEmpty($this->executedCommands);
    }

    public function testSearchEmptyQueryReturnsError(): void
    {
        $result = $this->reader->handleToolCall('journal_search', [
            'unit' => 'nginx',
            'query' => '',
        ]);

        $this->assertTrue($result['isError']);
        $this->assertEmpty($this->executedCommands);
    }

    public function testSearchFiltersBySubstring(): void
    {
        $this->fakeOutput = implode("\n", [
            '2026-07-18T10:00:01+0300 host nginx[1]: GET /index 200',
            '2026-07-18T10:00:02+0300 host nginx[1]: ERROR upstream timed out',
            '2026-07-18T10:00:03+0300 host nginx[1]: GET /health 200',
            '2026-07-18T10:00:04+0300 host nginx[1]: ERROR connection refused',
        ]);

        $result = $this->reader->handleToolCall('journal_search', [
            'unit' => 'nginx',
            'query' => 'ERROR',
        ]);

        $lines = explode("\n", $result['content'][0]['text']);
        $this->assertCount(2, $lines);
        $this->assertStringContainsString('upstream timed out', $lines[0]);
        $this->assertStringContainsString('connection refused', $lines[1]);
    }

    public function testSearchFiltersByRegex(): void
    {
        $this->fakeOutput = implode("\n", [
            'status 200 ok',
            'status 404 not found',
            'status 502 bad gateway',
        ]);

        $result = $this->reader->handleToolCall('journal_search', [
            'unit' => 'nginx',
            'query' => 'status (4|5)\d{2}',
            'regex' => true,
        ]);

        $lines = explode("\n", $result['content'][0]['text']);
        $this->assertCount(2, $lines);
    }

    public function testSearchInvalidRegexReturnsError(): void
    {
        $result = $this->reader->handleToolCall('journal_search', [
            'unit' => 'nginx',
            'query' => '([unclosed',
            'regex' => true,
        ]);

        $this->assertTrue($result['isError']);
        $this->assertStringContainsString('Invalid regex', $result['content'][0]['text']);
        $this->assertEmpty($this->executedCommands);
    }

    public function testSearchLimitsMatches(): void
    {
        $this->fakeOutput = implode("\n", array_fill(0, 50, 'ERROR repeated line'));

        $result = $this->reader->handleToolCall('journal_search', [
            'unit' => 'nginx',
            'query' => 'ERROR',
            'lines' => 10,
        ]);

        $this->assertCount(10, explode("\n", $result['content'][0]['text']));
    }

    public function testSearchQueryNeverReachesShell(): void
    {
        $this->fakeOutput = "nothing here\n";
        $query = '$(reboot); `rm -rf /`';

        $result = $this->reader->handleToolCall('journal_search', [
            'unit' => 'nginx',
            'query' => $query,
        ]);

        $this->assertArrayNotHasKey('isError', $result);
        $this->assertCount(1, $this->executedCommands);
        $this->assertStringNotContainsString('reboot', $this->executedCommands[0]);
        $this->assertStringNotContainsString('rm -rf', $this->executedCommands[0]);
    }

    public function testSearchPassesEscapedSinceUntil(): void
    {
        $this->fakeOutput = '';

        $this->reader->handleToolCall('journal_search', [
            'unit' => 'nginx',
            'query' => 'ERROR',
            'since' => '3 days ago',
            'until' => '2026-07-18 10:00:00',
        ]);

        $command = $this->executedCommands[0];
        $this->assertStringContainsString("--since '3 days ago'", $command);
        $this->assertStringContainsString("--until '2026-07-18 10:00:00'", $command);
    }

    public function testSearchRejectsMalformedTimeSpec(): void
    {
        foreach (['$(reboot)', 'now; rm -rf /', str_repeat('a', 65)] as $since) {
            $result = $this->reader->handleToolCall('journal_search', [
                'unit' => 'nginx',
                'query' => 'ERROR',
                'since' => $since,
            ]);

            $this->assertTrue($result['isError'], "since '{$since}' must be rejected");
        }

        $this->assertEmpty($this->executedCommands);
    }

    public function testSearchNoMatches(): void
    {
        $this->fakeOutput = "some journal line\n";

        $result = $this->reader->handleToolCall('journal_search', [
            'unit' => 'nginx',
            'query' => 'NONEXISTENT_XYZ',
        ]);

        $this->assertStringContainsString('No matches', $result['content'][0]['text']);
    }

    private function makeReader(bool $enabled = true): JournalReader
    {
        $executor = function (string $command): ?string {
            $this->executedCommands[] = $command;
            return $this->fakeOutput;
        };

        return new JournalReader($this->makeConfig($enabled), $executor);
    }

    private function makeConfig(bool $enabled): Config
    {
        $data = [
            'server' => ['host' => '127.0.0.1', 'transport' => 'stdio'],
            'journal' => [
                'enabled' => $enabled,
                'allowed_units' => ['nginx', 'postgresql', 'php8.2-fpm'],
            ],
        ];
        $tmp = tempnam(sys_get_temp_dir(), 'sl_');
        file_put_contents($tmp, \Symfony\Component\Yaml\Yaml::dump($data, 10));
        $config = Config::load($tmp);
        unlink($tmp);
        return $config;
    }
}
