<?php

declare(strict_types=1);

namespace Tests;

use PHPUnit\Framework\TestCase;
use ServerLens\Security\Redactor;

final class RedactorTest extends TestCase
{
    private Redactor $redactor;

    protected function setUp(): void
    {
        $this->redactor = new Redactor();
    }

    public function testRedactsPassword(): void
    {
        $input = "password = my_secret_pass123";
        $result = $this->redactor->redact($input);

        $this->assertStringNotContainsString('my_secret_pass123', $result);
        $this->assertStringContainsString('[REDACTED]', $result);
    }

    public function testRedactsApiKey(): void
    {
        $input = "api_key = sk-1234567890abcdef";
        $result = $this->redactor->redact($input);

        $this->assertStringNotContainsString('sk-1234567890abcdef', $result);
        $this->assertStringContainsString('[REDACTED]', $result);
    }

    public function testRedactsToken(): void
    {
        $input = "auth_token: bearer_abc123xyz";
        $result = $this->redactor->redact($input);

        $this->assertStringNotContainsString('bearer_abc123xyz', $result);
    }

    public function testRedactsDatabaseUrl(): void
    {
        $input = "database_url = postgres://user:pass@host/db";
        $result = $this->redactor->redact($input);

        $this->assertStringNotContainsString('postgres://user:pass@host/db', $result);
    }

    public function testPreservesNormalValues(): void
    {
        $input = "listen 80;\nserver_name example.com;\nworker_connections 1024;";
        $result = $this->redactor->redact($input);

        $this->assertSame($input, $result);
    }

    public function testCustomRedactRuleString(): void
    {
        $input = "custom_secret = very_private_data";
        $result = $this->redactor->redact($input, ['custom_secret']);

        $this->assertStringNotContainsString('very_private_data', $result);
        $this->assertStringContainsString('[REDACTED]', $result);
    }

    public function testCustomRedactRulePattern(): void
    {
        $input = "MY_SETTING=abc123\nNORMAL=ok";
        $result = $this->redactor->redact($input, [
            ['pattern' => 'MY_SETTING=\S+', 'replacement' => 'MY_SETTING=[REDACTED]'],
        ]);

        $this->assertStringContainsString('MY_SETTING=[REDACTED]', $result);
        $this->assertStringContainsString('NORMAL=ok', $result);
    }

    public function testCaseInsensitive(): void
    {
        $input = "PASSWORD = secret\nPassword = secret2\npassword = secret3";
        $result = $this->redactor->redact($input);

        $this->assertStringNotContainsString('secret', $result);
        $this->assertSame(3, substr_count($result, '[REDACTED]'));
    }
}
