<?php

declare(strict_types=1);

namespace Tests;

use PHPUnit\Framework\TestCase;
use ServerLens\Auth\TokenAuth;
use ServerLens\Config;

final class TokenAuthTest extends TestCase
{
    public function testGenerateTokenFormat(): void
    {
        $token = TokenAuth::generateToken();

        $this->assertStringStartsWith('sl_', $token);
        $this->assertSame(67, strlen($token)); // sl_ + 64 hex chars
    }

    public function testHashAndVerify(): void
    {
        $token = TokenAuth::generateToken();
        $hash = TokenAuth::hashToken($token);

        $this->assertStringStartsWith('$argon2id$', $hash);
        $this->assertTrue(password_verify($token, $hash));
    }

    public function testVerifyValidToken(): void
    {
        $token = TokenAuth::generateToken();
        $hash = TokenAuth::hashToken($token);

        $config = $this->makeConfig($hash);
        $auth = new TokenAuth($config);

        $this->assertTrue($auth->verify("Bearer {$token}"));
    }

    public function testVerifyInvalidToken(): void
    {
        $hash = TokenAuth::hashToken('sl_real_token');
        $config = $this->makeConfig($hash);
        $auth = new TokenAuth($config);

        $this->assertFalse($auth->verify('Bearer sl_wrong_token'));
    }

    public function testVerifyMissingBearer(): void
    {
        $config = $this->makeConfig('$argon2id$fake');
        $auth = new TokenAuth($config);

        $this->assertFalse($auth->verify(''));
        $this->assertFalse($auth->verify('Token abc'));
    }

    public function testExpiredTokenRejected(): void
    {
        $token = TokenAuth::generateToken();
        $hash = TokenAuth::hashToken($token);

        $config = $this->makeConfig($hash, '2020-01-01');
        $auth = new TokenAuth($config);

        $this->assertFalse($auth->verify("Bearer {$token}"));
    }

    public function testLockoutAfterMaxFailed(): void
    {
        $token = TokenAuth::generateToken();
        $hash = TokenAuth::hashToken($token);

        $config = $this->makeConfig($hash);
        $auth = new TokenAuth($config);

        for ($i = 0; $i < 3; $i++) {
            $auth->verify('Bearer wrong', '10.0.0.1');
        }

        $this->assertFalse(
            $auth->verify("Bearer {$token}", '10.0.0.1'),
            'Should be locked out after 3 failures'
        );

        $this->assertTrue(
            $auth->verify("Bearer {$token}", '10.0.0.2'),
            'Different IP should not be locked'
        );
    }

    private function makeConfig(string $hash, string $expires = '2030-12-31'): Config
    {
        $yaml = [
            'server' => ['host' => '127.0.0.1', 'port' => 9600, 'transport' => 'stdio'],
            'auth' => [
                'tokens' => [['hash' => $hash, 'created' => '2026-01-01', 'expires' => $expires]],
                'max_failed_attempts' => 3,
                'lockout_minutes' => 15,
            ],
        ];
        $tmp = tempnam(sys_get_temp_dir(), 'sl_');
        file_put_contents($tmp, \Symfony\Component\Yaml\Yaml::dump($yaml, 10));

        $config = Config::load($tmp);
        unlink($tmp);
        return $config;
    }
}
