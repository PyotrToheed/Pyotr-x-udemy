// Udemy Cookie Exporter - Chrome Extension

const IMPORTANT_COOKIES = [
  "access_token", "client_id", "ud_user_jwt", "csrftoken",
  "cf_clearance", "__cf_bm", "__cfruid", "dj_session_id",
  "ud_cache_user", "ud_cache_logged_in",
];

let cookieText = "";

function formatCookies(cookies) {
  const header = [
    "# Netscape HTTP Cookie File",
    "# https://curl.haxx.se/rfc/cookie_spec.html",
    "# This is a generated file! Do not edit.",
    "",
  ].join("\n");

  const lines = cookies.map((c) => {
    const domain = c.domain.startsWith(".") ? c.domain : "." + c.domain;
    const includeSubdomains = domain.startsWith(".") ? "TRUE" : "FALSE";
    const path = c.path || "/";
    const secure = c.secure ? "TRUE" : "FALSE";
    const expires = c.expirationDate ? Math.floor(c.expirationDate) : 0;
    const name = c.name;
    const value = c.value;
    return `${domain}\t${includeSubdomains}\t${path}\t${secure}\t${expires}\t${name}\t${value}`;
  });

  return header + lines.join("\n") + "\n";
}

function updateStatus(cookies) {
  const box = document.getElementById("statusBox");
  const found = {};
  for (const c of cookies) {
    found[c.name] = c.value;
  }

  let html = `<div><span class="label">Total cookies:</span> <span class="value">${cookies.length}</span></div>`;

  const checks = [
    ["access_token", "Access Token"],
    ["client_id", "Client ID"],
    ["csrftoken", "CSRF Token"],
    ["cf_clearance", "CF Clearance"],
  ];

  for (const [name, label] of checks) {
    const has = name in found;
    const cls = has ? "value" : "value missing";
    const txt = has ? "found" : "missing";
    html += `<div><span class="label">${label}:</span> <span class="${cls}">${txt}</span></div>`;
  }

  if (found["ud_cache_user"]) {
    html += `<div><span class="label">User ID:</span> <span class="value">${found["ud_cache_user"]}</span></div>`;
  }

  box.innerHTML = html;
}

async function loadCookies() {
  try {
    const cookies = await chrome.cookies.getAll({ domain: "udemy.com" });
    const cookies2 = await chrome.cookies.getAll({ domain: ".udemy.com" });
    const cookiesWww = await chrome.cookies.getAll({ domain: "www.udemy.com" });

    // Deduplicate by name+domain
    const seen = new Set();
    const all = [];
    for (const c of [...cookies, ...cookies2, ...cookiesWww]) {
      const key = `${c.domain}|${c.name}`;
      if (!seen.has(key)) {
        seen.add(key);
        all.push(c);
      }
    }

    updateStatus(all);

    if (all.length > 0) {
      cookieText = formatCookies(all);
      document.getElementById("exportBtn").disabled = false;
      document.getElementById("copyBtn").disabled = false;
    } else {
      document.getElementById("msg").textContent = "No Udemy cookies found. Log in first.";
      document.getElementById("msg").classList.add("error");
    }
  } catch (err) {
    document.getElementById("statusBox").textContent = "Error: " + err.message;
  }
}

// Export as .txt file download
document.getElementById("exportBtn").addEventListener("click", () => {
  const blob = new Blob([cookieText], { type: "text/plain" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = "udemy_cookies.txt";
  a.click();
  URL.revokeObjectURL(url);
  document.getElementById("msg").textContent = "Cookies exported!";
  document.getElementById("msg").classList.remove("error");
});

// Copy to clipboard
document.getElementById("copyBtn").addEventListener("click", async () => {
  try {
    await navigator.clipboard.writeText(cookieText);
    document.getElementById("msg").textContent = "Copied to clipboard!";
    document.getElementById("msg").classList.remove("error");
  } catch {
    // Fallback
    const ta = document.createElement("textarea");
    ta.value = cookieText;
    document.body.appendChild(ta);
    ta.select();
    document.execCommand("copy");
    document.body.removeChild(ta);
    document.getElementById("msg").textContent = "Copied!";
    document.getElementById("msg").classList.remove("error");
  }
});

// Load on popup open
loadCookies();
