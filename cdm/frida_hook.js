/**
 * Frida hook script for manual Widevine L3 CDM extraction.
 *
 * This script hooks OEMCrypto functions in libwvdrmengine.so to capture
 * the RSA private key and client ID during DRM initialization.
 *
 * Usage:
 *   frida -U -f com.android.chrome -l frida_hook.js
 *   # OR
 *   frida -U -n "Google Chrome" -l frida_hook.js
 *   # Then play any DRM video in the browser to trigger key extraction
 *
 * What this hooks:
 *   - OEMCrypto_LoadDeviceRSAKey (or obfuscated variants)
 *   - Captures the RSA private key buffer and client ID
 *
 * Note: Function names are often obfuscated in production builds.
 *       Use check_symbols.py or `nm` to find actual symbol names.
 *       Common obfuscated patterns: _lcc04, _lcc07, _oecc04, _oecc07
 */

'use strict';

// ============================================================
// Configuration
// ============================================================

// Library to hook - change if your device uses a different lib
const TARGET_LIB = 'libwvdrmengine.so';

// Known OEMCrypto function patterns (obfuscated names vary by device)
// These are common patterns found in various Android builds
const OEMCRYPTO_PATTERNS = [
    // Standard names (rare in production)
    'OEMCrypto_LoadDeviceRSAKey',
    'OEMCrypto_GenerateSignature',
    'OEMCrypto_GenerateDerivedKeys',
    // Common obfuscated patterns
    '_lcc04', '_lcc07', '_lcc12',
    '_oecc04', '_oecc07', '_oecc12',
    '_oecc15', '_oecc16',
];

// ============================================================
// Hooking Logic
// ============================================================

function hexdump_short(buf, len) {
    var arr = [];
    for (var i = 0; i < Math.min(len, 64); i++) {
        arr.push(('0' + buf.add(i).readU8().toString(16)).slice(-2));
    }
    return arr.join(' ') + (len > 64 ? '...' : '');
}

function isPEM(buf, len) {
    try {
        var str = buf.readUtf8String(Math.min(len, 40));
        return str && str.indexOf('-----BEGIN') >= 0;
    } catch (e) {
        return false;
    }
}

function hookFunction(addr, name) {
    Interceptor.attach(addr, {
        onEnter: function(args) {
            console.log('\n[*] ' + name + ' called');

            // Log first 6 arguments (pointers/values)
            for (var i = 0; i < 6; i++) {
                try {
                    var val = args[i];
                    console.log('    arg[' + i + '] = ' + val);

                    // Check if it looks like a buffer with PEM data
                    if (!val.isNull()) {
                        try {
                            if (isPEM(val, 100)) {
                                var pemStr = val.readUtf8String(2048);
                                console.log('\n[!!!] RSA PRIVATE KEY FOUND in arg[' + i + ']:');
                                console.log(pemStr);
                                console.log('\n[*] Save this key to private_key.pem');

                                // Write to file
                                var f = new File('/data/local/tmp/private_key.pem', 'w');
                                f.write(pemStr);
                                f.close();
                                console.log('[*] Saved to /data/local/tmp/private_key.pem');
                            }
                        } catch (e) {}
                    }
                } catch (e) {}
            }
        },
        onLeave: function(retval) {
            console.log('    return = ' + retval);
        }
    });
    console.log('[+] Hooked: ' + name + ' @ ' + addr);
}

function findAndHook() {
    console.log('[*] Searching for ' + TARGET_LIB + '...');

    var mod = Process.findModuleByName(TARGET_LIB);
    if (!mod) {
        console.log('[-] Library not loaded yet. Will retry...');
        return false;
    }

    console.log('[+] Found: ' + mod.name + ' @ ' + mod.base + ' (' + mod.size + ' bytes)');

    // Enumerate exports and match patterns
    var exports = mod.enumerateExports();
    var hooked = 0;

    for (var i = 0; i < exports.length; i++) {
        var exp = exports[i];
        for (var j = 0; j < OEMCRYPTO_PATTERNS.length; j++) {
            if (exp.name.indexOf(OEMCRYPTO_PATTERNS[j]) >= 0) {
                hookFunction(exp.address, exp.name);
                hooked++;
                break;
            }
        }
    }

    if (hooked === 0) {
        console.log('[-] No matching exports found. Dumping all exports:');
        for (var k = 0; k < Math.min(exports.length, 50); k++) {
            console.log('    ' + exports[k].name);
        }
        console.log('    ... (' + exports.length + ' total)');
        console.log('\n[!] Try adding your device\'s obfuscated function names to OEMCRYPTO_PATTERNS');
    } else {
        console.log('[+] Hooked ' + hooked + ' functions. Play a DRM video to trigger extraction.');
    }

    return true;
}

// ============================================================
// Entry Point
// ============================================================

console.log('');
console.log('==============================================');
console.log('  Widevine L3 CDM Extraction - Frida Hook');
console.log('==============================================');
console.log('  Target: ' + TARGET_LIB);
console.log('');

// Try immediate hook, otherwise wait for library load
if (!findAndHook()) {
    console.log('[*] Waiting for library to load...');

    var checkInterval = setInterval(function() {
        if (findAndHook()) {
            clearInterval(checkInterval);
        }
    }, 1000);

    // Also hook dlopen to catch library loading
    try {
        Interceptor.attach(Module.findExportByName(null, 'dlopen'), {
            onEnter: function(args) {
                this.path = args[0].readUtf8String();
            },
            onLeave: function(retval) {
                if (this.path && this.path.indexOf('wvdrm') >= 0) {
                    console.log('[*] dlopen: ' + this.path);
                    setTimeout(findAndHook, 500);
                }
            }
        });
    } catch (e) {}
}
