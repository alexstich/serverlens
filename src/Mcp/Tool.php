<?php

declare(strict_types=1);

namespace ServerLens\Mcp;

final class Tool
{
    public function __construct(
        public readonly string $name,
        public readonly string $description,
        public readonly array $inputSchema,
    ) {}

    public function toArray(): array
    {
        return [
            'name' => $this->name,
            'description' => $this->description,
            'inputSchema' => $this->inputSchema,
        ];
    }
}
