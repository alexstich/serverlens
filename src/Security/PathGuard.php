<?php

declare(strict_types=1);

namespace ServerLens\Security;

final class PathGuard
{
    /** @var string[] resolved allowed directories */
    private array $allowedDirs = [];

    /** @var array<string, string> name => resolved path */
    private array $allowedPaths = [];

    /**
     * @param array<array{name: string, path: string}> $sources
     */
    public function registerSources(array $sources): void
    {
        foreach ($sources as $source) {
            $name = $source['name'];
            $path = $source['path'];
            $type = $source['type'] ?? 'file';

            $resolved = realpath($path);
            if ($resolved === false) {
                fwrite(STDERR, "[ServerLens] Warning: path not found for source '{$name}': {$path}\n");
                $this->allowedPaths[$name] = $path;
                continue;
            }

            $this->allowedPaths[$name] = $resolved;

            if ($type === 'directory' || is_dir($resolved)) {
                $this->allowedDirs[] = rtrim($resolved, '/') . '/';
            }
        }
    }

    public function getResolvedPath(string $name): ?string
    {
        return $this->allowedPaths[$name] ?? null;
    }

    public function isAllowed(string $path): bool
    {
        $resolved = realpath($path);
        if ($resolved === false) {
            return false;
        }

        if (in_array($resolved, $this->allowedPaths, true)) {
            return true;
        }

        foreach ($this->allowedDirs as $dir) {
            if (str_starts_with($resolved, $dir)) {
                return true;
            }
        }

        return false;
    }

    public function validateSource(string $name): ?string
    {
        $path = $this->getResolvedPath($name);
        if ($path === null) {
            return null;
        }

        $resolved = realpath($path);
        if ($resolved === false) {
            return null;
        }

        if ($resolved !== $path && !$this->isInAllowedDir($resolved)) {
            return null;
        }

        return $resolved;
    }

    private function isInAllowedDir(string $path): bool
    {
        foreach ($this->allowedDirs as $dir) {
            if (str_starts_with($path, $dir)) {
                return true;
            }
        }

        return false;
    }
}
