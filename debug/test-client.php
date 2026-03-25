<?php
/**
 * Test client for ServerLens MCP server.
 * Maintains a persistent SSE connection and sends POST requests.
 */

$token = $argv[1] ?? 'sl_0d2625206c189eab115322a89ac97190e6670fad9fc7c527f8ef184fd210f3fa';
$host = '127.0.0.1';
$port = 9600;

echo "=== ServerLens Test Client ===\n\n";

// Open persistent SSE connection using raw sockets
echo "[1] Opening SSE connection...\n";
$sseSock = stream_socket_client("tcp://{$host}:{$port}", $errno, $errstr, 5);
if (!$sseSock) {
    die("Cannot connect: {$errstr}\n");
}

$sseRequest = "GET /sse HTTP/1.1\r\n" .
    "Host: {$host}:{$port}\r\n" .
    "Authorization: Bearer {$token}\r\n" .
    "Accept: text/event-stream\r\n" .
    "Cache-Control: no-cache\r\n" .
    "\r\n";

fwrite($sseSock, $sseRequest);
stream_set_timeout($sseSock, 3);

$sessionEndpoint = null;
$headersDone = false;
$buffer = '';

while (!$sessionEndpoint) {
    $chunk = fread($sseSock, 4096);
    if ($chunk === false || $chunk === '') {
        $info = stream_get_meta_data($sseSock);
        if ($info['timed_out']) {
            echo "  Timeout waiting for SSE endpoint\n";
            break;
        }
        break;
    }
    $buffer .= $chunk;

    if (preg_match('/data:\s*(\/message\?sessionId=[a-f0-9]+)/', $buffer, $m)) {
        $sessionEndpoint = trim($m[1]);
    }
}

if (!$sessionEndpoint) {
    echo "  ERROR: No session endpoint received\n";
    echo "  Buffer: " . json_encode($buffer) . "\n";
    fclose($sseSock);
    exit(1);
}

echo "  Session: {$sessionEndpoint}\n\n";

$messageUrl = "http://{$host}:{$port}{$sessionEndpoint}";

// Helper: send POST and read SSE response
function sendAndRead(string $url, string $token, array $message, $sseSock): ?string
{
    $ch = curl_init($url);
    curl_setopt_array($ch, [
        CURLOPT_POST => true,
        CURLOPT_HTTPHEADER => [
            "Authorization: Bearer {$token}",
            'Content-Type: application/json',
        ],
        CURLOPT_POSTFIELDS => json_encode($message),
        CURLOPT_RETURNTRANSFER => true,
        CURLOPT_TIMEOUT => 5,
    ]);
    $body = curl_exec($ch);
    $httpCode = curl_getinfo($ch, CURLINFO_HTTP_CODE);
    curl_close($ch);

    if ($httpCode !== 202) {
        echo "  POST returned HTTP {$httpCode}: {$body}\n";
        return null;
    }

    stream_set_timeout($sseSock, 3);
    $buffer = '';
    while (true) {
        $chunk = fread($sseSock, 8192);
        if ($chunk === false || $chunk === '') {
            $info = stream_get_meta_data($sseSock);
            if ($info['timed_out']) {
                break;
            }
            break;
        }
        $buffer .= $chunk;

        if (preg_match('/data:\s*(\{.*"jsonrpc".*\})/', $buffer, $m)) {
            return $m[1];
        }
    }

    return $buffer ?: null;
}

// Step 2: Initialize
echo "[2] Initialize...\n";
$result = sendAndRead($messageUrl, $token, [
    'jsonrpc' => '2.0',
    'id' => 1,
    'method' => 'initialize',
    'params' => [
        'protocolVersion' => '2024-11-05',
        'capabilities' => new stdClass(),
        'clientInfo' => ['name' => 'test-client', 'version' => '1.0'],
    ],
], $sseSock);
if ($result) {
    $parsed = json_decode($result, true);
    $serverName = $parsed['result']['serverInfo']['name'] ?? 'unknown';
    $version = $parsed['result']['serverInfo']['version'] ?? 'unknown';
    echo "  Server: {$serverName} v{$version}\n";
} else {
    echo "  ERROR: No response\n";
}
echo "\n";

// Send initialized notification
sendAndRead($messageUrl, $token, [
    'jsonrpc' => '2.0',
    'method' => 'notifications/initialized',
], $sseSock);

// Step 3: List tools
echo "[3] tools/list...\n";
$result = sendAndRead($messageUrl, $token, [
    'jsonrpc' => '2.0',
    'id' => 2,
    'method' => 'tools/list',
    'params' => [],
], $sseSock);
if ($result) {
    $parsed = json_decode($result, true);
    if (isset($parsed['result']['tools'])) {
        foreach ($parsed['result']['tools'] as $tool) {
            echo "  - {$tool['name']}: {$tool['description']}\n";
        }
    }
}
echo "\n";

// Step 4: logs_list
echo "[4] logs_list...\n";
$result = sendAndRead($messageUrl, $token, [
    'jsonrpc' => '2.0',
    'id' => 3,
    'method' => 'tools/call',
    'params' => ['name' => 'logs_list', 'arguments' => []],
], $sseSock);
if ($result) {
    $parsed = json_decode($result, true);
    $text = $parsed['result']['content'][0]['text'] ?? $result;
    echo "  {$text}\n";
}
echo "\n";

// Step 5: logs_tail
echo "[5] logs_tail (last 3 lines)...\n";
$result = sendAndRead($messageUrl, $token, [
    'jsonrpc' => '2.0',
    'id' => 4,
    'method' => 'tools/call',
    'params' => ['name' => 'logs_tail', 'arguments' => ['source' => 'test_app', 'lines' => 3]],
], $sseSock);
if ($result) {
    $parsed = json_decode($result, true);
    $text = $parsed['result']['content'][0]['text'] ?? $result;
    echo "  {$text}\n";
}
echo "\n";

// Step 6: logs_search
echo "[6] logs_search (query: ERROR)...\n";
$result = sendAndRead($messageUrl, $token, [
    'jsonrpc' => '2.0',
    'id' => 5,
    'method' => 'tools/call',
    'params' => ['name' => 'logs_search', 'arguments' => ['source' => 'test_app', 'query' => 'ERROR']],
], $sseSock);
if ($result) {
    $parsed = json_decode($result, true);
    $text = $parsed['result']['content'][0]['text'] ?? $result;
    echo "  {$text}\n";
}
echo "\n";

// Step 7: logs_count
echo "[7] logs_count...\n";
$result = sendAndRead($messageUrl, $token, [
    'jsonrpc' => '2.0',
    'id' => 6,
    'method' => 'tools/call',
    'params' => ['name' => 'logs_count', 'arguments' => ['source' => 'test_app']],
], $sseSock);
if ($result) {
    $parsed = json_decode($result, true);
    $text = $parsed['result']['content'][0]['text'] ?? $result;
    echo "  {$text}\n";
}
echo "\n";

fclose($sseSock);
echo "=== All tests complete ===\n";
