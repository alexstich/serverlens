<?php

declare(strict_types=1);

namespace ServerLens\Module;

use ServerLens\Mcp\Tool;

interface ModuleInterface
{
    /** @return Tool[] */
    public function getTools(): array;

    /**
     * @return array{content: array<array{type: string, text: string}>, isError?: bool}
     */
    public function handleToolCall(string $name, array $arguments): array;
}
