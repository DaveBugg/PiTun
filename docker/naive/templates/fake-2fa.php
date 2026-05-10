<?php
// Decoy 2FA login page (since v1.3.0-beta.6).
//
// Threat model: the visitor is an IT-aware investigator (curl, view-source,
// DevTools Network tab). Pure-static decoys betray themselves on a POST
// because there's no real server roundtrip — Caddy returns 404 for /verify,
// and browser dev tools surface that immediately. This file gives the
// roundtrip: real session state, real CSRF, real network delay, real
// progressive error messages. There is no real 2FA validation — every code
// is rejected with a plausible-looking error sequence.
//
// Hardening relies on the php.ini jail dropped by setup-naive-server.sh
// (see /etc/php/PHP_VER/fpm/conf.d/99-pitun-decoy.ini): disable_functions
// blocks exec/eval/shell/popen/etc., allow_url_fopen=Off blocks SSRF,
// open_basedir jails the FS, and php-fpm runs as `caddy` (no shell).
// Even if every line below were exploitable, RCE/SSRF/data-exfil would
// still hit a wall.

session_set_cookie_params([
    'httponly' => true,
    'secure'   => isset($_SERVER['HTTPS']) || ($_SERVER['HTTP_X_FORWARDED_PROTO'] ?? '') === 'https',
    'samesite' => 'Lax',
]);
session_start();

if (empty($_SESSION['csrf'])) {
    $_SESSION['csrf'] = bin2hex(random_bytes(16));
}
if (!isset($_SESSION['attempts'])) {
    $_SESSION['attempts'] = 0;
}
$csrf = $_SESSION['csrf'];

// Progressive error messages — the visitor sees the same UX they'd get
// from a real corporate 2FA portal: first try is generic, second hints at
// resync, third locks out for 30 minutes. None of these states correspond
// to anything real, but they look plausible across multiple attempts.
$error = null;
$locked_until = $_SESSION['locked_until'] ?? 0;
$now = time();
if ($locked_until > $now) {
    $minutes = (int)ceil(($locked_until - $now) / 60);
    $error = "Account temporarily locked. Try again in {$minutes} minute" . ($minutes === 1 ? '' : 's') . '.';
}

if ($_SERVER['REQUEST_METHOD'] === 'POST' && $error === null) {
    // Constant-ish delay so the response timing doesn't betray that no
    // real backend computation happened. Real corporate 2FA endpoints
    // sit behind layered auth services and round-trip in ~300-700 ms.
    usleep(random_int(350000, 650000));

    $submitted_csrf = $_POST['csrf'] ?? '';
    $code = trim((string)($_POST['code'] ?? ''));

    if (!hash_equals($csrf, $submitted_csrf)) {
        $error = 'Session expired. Please reload and try again.';
    } elseif (!preg_match('/^\d{6}$/', $code)) {
        $error = 'Code must be exactly 6 digits.';
    } else {
        $_SESSION['attempts']++;
        $a = $_SESSION['attempts'];
        if ($a >= 5) {
            $_SESSION['locked_until'] = $now + 1800;
            $error = 'Too many failed attempts. Account locked for 30 minutes.';
        } elseif ($a >= 3) {
            $error = 'Invalid code. If your authenticator app is out of sync, please re-pair the device from your account settings.';
        } else {
            $error = 'Invalid verification code. Please try again.';
        }
    }
}

$user_email = htmlspecialchars($_SESSION['user_email'] ?? 'a***@company.com', ENT_QUOTES, 'UTF-8');
$attempts_left = max(0, 5 - (int)$_SESSION['attempts']);
?><!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow">
<title>Two-Factor Authentication</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
html,body{height:100%;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;color:#1a202c;background:#f1f5f9}
body{display:flex;align-items:center;justify-content:center;padding:1rem}
.card{background:#fff;border-radius:12px;box-shadow:0 4px 24px rgba(15,23,42,.08);padding:2.5rem 2rem;max-width:420px;width:100%}
.brand{display:flex;align-items:center;gap:.625rem;margin-bottom:1.75rem;color:#0f172a}
.brand-mark{width:32px;height:32px;border-radius:8px;background:linear-gradient(135deg,#3b82f6,#1d4ed8);display:flex;align-items:center;justify-content:center;color:#fff;font-weight:700}
.brand-name{font-weight:600;font-size:1.05rem}
h1{font-size:1.35rem;font-weight:600;margin-bottom:.4rem;color:#0f172a}
.lede{color:#475569;font-size:.92rem;margin-bottom:1.5rem;line-height:1.5}
.lede strong{color:#0f172a}
form{display:flex;flex-direction:column;gap:1rem}
label{font-size:.85rem;font-weight:500;color:#334155}
.code-input{font-family:"SFMono-Regular",Menlo,Consolas,monospace;font-size:1.5rem;letter-spacing:.5em;text-align:center;padding:.875rem .75rem;border:1px solid #cbd5e0;border-radius:8px;width:100%;outline:none;transition:border-color .15s,box-shadow .15s}
.code-input:focus{border-color:#3b82f6;box-shadow:0 0 0 3px rgba(59,130,246,.15)}
.code-input.error{border-color:#dc2626;box-shadow:0 0 0 3px rgba(220,38,38,.12)}
.error-banner{background:#fef2f2;color:#991b1b;border:1px solid #fecaca;border-radius:8px;padding:.75rem 1rem;font-size:.88rem;line-height:1.4}
.btn{background:#1d4ed8;color:#fff;border:none;border-radius:8px;padding:.875rem 1rem;font-size:.95rem;font-weight:600;cursor:pointer;transition:background .12s}
.btn:hover{background:#1e40af}
.btn:disabled{background:#94a3b8;cursor:not-allowed}
.foot{margin-top:1.25rem;font-size:.82rem;color:#64748b;text-align:center;line-height:1.5}
.foot a{color:#3b82f6;text-decoration:none}
.foot a:hover{text-decoration:underline}
.attempts{font-size:.78rem;color:#64748b;margin-top:.5rem;text-align:right}
.spinner{display:inline-block;width:14px;height:14px;border:2px solid #fff;border-top-color:transparent;border-radius:50%;animation:spin .7s linear infinite;vertical-align:-2px;margin-right:.5rem}
@keyframes spin{to{transform:rotate(360deg)}}
</style>
</head>
<body>
<main class="card" role="main">
  <div class="brand">
    <div class="brand-mark" aria-hidden="true">A</div>
    <div class="brand-name">Aether Workspace</div>
  </div>
  <h1>Verify your identity</h1>
  <p class="lede">Enter the 6-digit code from your authenticator app to continue signing in as <strong><?= $user_email ?></strong>.</p>

  <?php if ($error !== null): ?>
    <div class="error-banner" role="alert"><?= htmlspecialchars($error, ENT_QUOTES, 'UTF-8') ?></div>
  <?php endif; ?>

  <form method="post" action="" autocomplete="off" novalidate id="totp-form">
    <input type="hidden" name="csrf" value="<?= htmlspecialchars($csrf, ENT_QUOTES, 'UTF-8') ?>">
    <label for="code">Authentication code</label>
    <input
      type="text"
      id="code"
      name="code"
      class="code-input<?= $error !== null ? ' error' : '' ?>"
      inputmode="numeric"
      pattern="[0-9]{6}"
      maxlength="6"
      autocomplete="one-time-code"
      autofocus
      required>
    <button type="submit" class="btn" id="submit-btn">Verify</button>
    <?php if ($attempts_left > 0 && $attempts_left < 5): ?>
      <div class="attempts"><?= $attempts_left ?> attempt<?= $attempts_left === 1 ? '' : 's' ?> remaining</div>
    <?php endif; ?>
  </form>

  <div class="foot">
    Lost access to your device? <a href="#" onclick="return false">Use a backup code</a>
    <br><a href="#" onclick="return false">Sign out</a>
  </div>
</main>
<script>
// Lightweight UX polish — autosubmit on full-length entry, busy state on
// submit, digit-only filter. None of this is security-relevant.
(function(){
  var input = document.getElementById('code');
  var form  = document.getElementById('totp-form');
  var btn   = document.getElementById('submit-btn');
  if (!input || !form || !btn) return;
  input.addEventListener('input', function(){
    this.value = this.value.replace(/\D/g, '').slice(0, 6);
    if (this.value.length === 6) form.requestSubmit();
  });
  form.addEventListener('submit', function(){
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner" aria-hidden="true"></span>Verifying...';
  });
})();
</script>
</body>
</html>
