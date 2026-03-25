<?php

declare(strict_types=1);

namespace ServerLens\Security;

final class Redactor
{
    private const BUILTIN_PATTERNS = [
        '/(?i)(password|passwd|pass)\s*[:=]\s*\S+/' => '$1: [REDACTED]',
        '/(?i)(secret|api_key|apikey|api-key)\s*[:=]\s*\S+/' => '$1: [REDACTED]',
        '/(?i)(token|auth_token|access_token)\s*[:=]\s*\S+/' => '$1: [REDACTED]',
        '/(?i)(private_key|private-key)\s*[:=]\s*\S+/' => '$1: [REDACTED]',
        '/(?i)(connection_string|dsn|database_url)\s*[:=]\s*\S+/' => '$1: [REDACTED]',
        '/(?i)(aws_secret|aws_access)\s*[:=]\s*\S+/' => '$1: [REDACTED]',
    ];

    /**
     * @param string $content
     * @param array  $sourceRedact  redaction rules from config (strings or pattern objects)
     */
    public function redact(string $content, array $sourceRedact = []): string
    {
        foreach (self::BUILTIN_PATTERNS as $pattern => $replacement) {
            $content = preg_replace($pattern, $replacement, $content) ?? $content;
        }

        foreach ($sourceRedact as $rule) {
            if (is_string($rule)) {
                $escaped = preg_quote($rule, '/');
                $pattern = "/(?i){$escaped}\s*[:=]\s*\S+/";
                $content = preg_replace($pattern, "{$rule}: [REDACTED]", $content) ?? $content;
            } elseif (is_array($rule) && isset($rule['pattern'], $rule['replacement'])) {
                $content = preg_replace(
                    '/' . $rule['pattern'] . '/',
                    $rule['replacement'],
                    $content
                ) ?? $content;
            }
        }

        return $content;
    }
}
