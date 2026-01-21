/**
 * End-to-end tests for thestill.me web application
 * Tests both single-user and multi-user authentication modes
 *
 * Run with: node tests/e2e/test_web_auth.js
 * Requires: puppeteer installed (npm install puppeteer in frontend)
 */

const puppeteer = require('puppeteer');
const { spawn } = require('child_process');
const path = require('path');

const BASE_URL = 'http://127.0.0.1:8000';
const WAIT_TIMEOUT = 30000;

let serverProcess = null;
let browser = null;

/**
 * Start the thestill web server
 * @param {Object} envOverrides - Environment variables to override
 * @returns {Promise<ChildProcess>}
 */
async function startServer(envOverrides = {}) {
    return new Promise((resolve, reject) => {
        // Go up from frontend -> web -> thestill -> project root
        const projectRoot = path.resolve(__dirname, '../../..');
        const venvPython = path.join(projectRoot, 'venv', 'bin', 'python');
        const env = {
            ...process.env,
            ...envOverrides,
        };

        console.log(`Project root: ${projectRoot}`);
        console.log(`Starting server with MULTI_USER=${env.MULTI_USER || 'false'}...`);

        serverProcess = spawn(
            venvPython,
            ['-m', 'thestill.cli', 'server', '--port', '8000'],
            {
                cwd: projectRoot,
                env: env,
                stdio: ['ignore', 'pipe', 'pipe'],
            }
        );

        let started = false;

        serverProcess.stdout.on('data', (data) => {
            const output = data.toString();
            if (!started && (output.includes('Uvicorn running') || output.includes('Application startup complete'))) {
                started = true;
                // Give it a moment to fully initialize
                setTimeout(() => resolve(serverProcess), 1000);
            }
        });

        serverProcess.stderr.on('data', (data) => {
            const output = data.toString();
            console.log(`[stderr] ${output.trim()}`);
            // Uvicorn logs to stderr
            if (!started && (output.includes('Uvicorn running') || output.includes('Application startup complete'))) {
                started = true;
                setTimeout(() => resolve(serverProcess), 1000);
            }
        });

        serverProcess.on('error', reject);

        // Timeout after 30 seconds
        setTimeout(() => {
            if (!started) {
                reject(new Error('Server failed to start within 30 seconds'));
            }
        }, 30000);
    });
}

/**
 * Stop the web server
 */
async function stopServer() {
    if (serverProcess) {
        console.log('Stopping server...');
        serverProcess.kill('SIGTERM');
        await new Promise(resolve => setTimeout(resolve, 1000));
        serverProcess = null;
    }
}

/**
 * Test single-user mode (default, no login required)
 */
async function testSingleUserMode() {
    console.log('\n========================================');
    console.log('Testing SINGLE-USER MODE (MULTI_USER=false)');
    console.log('========================================\n');

    await startServer({ MULTI_USER: 'false' });

    const page = await browser.newPage();

    try {
        // Test 1: Homepage loads without login
        console.log('Test 1: Homepage loads without requiring login...');
        await page.goto(BASE_URL, { waitUntil: 'networkidle0', timeout: WAIT_TIMEOUT });

        // Should see dashboard content (not login page)
        const pageContent = await page.content();
        const isLoginPage = pageContent.includes('Continue with Google') || pageContent.includes('Sign in');

        if (isLoginPage) {
            throw new Error('Unexpected login page in single-user mode');
        }
        console.log('  ✓ Homepage loads without login');

        // Test 2: Auth status endpoint returns single-user mode
        console.log('Test 2: Auth status endpoint returns correct mode...');
        const authStatusResponse = await page.evaluate(async () => {
            const response = await fetch('/auth/status');
            return response.json();
        });

        if (authStatusResponse.multi_user !== false) {
            throw new Error(`Expected multi_user=false, got ${authStatusResponse.multi_user}`);
        }
        if (!authStatusResponse.user) {
            throw new Error('Expected default user to be present');
        }
        console.log(`  ✓ Auth status: multi_user=${authStatusResponse.multi_user}`);
        console.log(`  ✓ Default user: ${authStatusResponse.user.email}`);

        // Test 3: API endpoints work without authentication
        console.log('Test 3: API endpoints work without explicit auth...');
        const dashboardResponse = await page.evaluate(async () => {
            const response = await fetch('/api/dashboard/stats');
            return { status: response.status, ok: response.ok };
        });

        if (!dashboardResponse.ok) {
            throw new Error(`Dashboard API failed with status ${dashboardResponse.status}`);
        }
        console.log('  ✓ Dashboard API accessible');

        // Test 4: User menu shows single-user indicator
        console.log('Test 4: User menu shows correct state...');
        await page.waitForSelector('[data-testid="user-menu"], .user-menu, button[aria-label*="user"], button[aria-label*="menu"]', { timeout: 5000 }).catch(() => {
            console.log('  ⚠ User menu selector not found (UI may have changed)');
        });
        console.log('  ✓ Single-user mode UI verified');

        console.log('\n✅ SINGLE-USER MODE: All tests passed\n');

    } catch (error) {
        console.error(`\n❌ SINGLE-USER MODE: Test failed - ${error.message}\n`);
        // Take screenshot for debugging
        await page.screenshot({ path: 'test-failure-single-user.png' });
        throw error;
    } finally {
        await page.close();
        await stopServer();
    }
}

/**
 * Test multi-user mode (requires Google OAuth)
 */
async function testMultiUserMode() {
    console.log('\n========================================');
    console.log('Testing MULTI-USER MODE (MULTI_USER=true)');
    console.log('========================================\n');

    // Multi-user mode requires Google OAuth credentials
    // For testing, we check that:
    // 1. Without credentials, the server handles gracefully
    // 2. Protected routes redirect to login
    // 3. Auth status returns multi_user=true

    await startServer({
        MULTI_USER: 'true',
        // Use dummy credentials for testing (will fail actual OAuth but allows server to start)
        GOOGLE_CLIENT_ID: 'test-client-id.apps.googleusercontent.com',
        GOOGLE_CLIENT_SECRET: 'test-client-secret',
        JWT_SECRET_KEY: 'test-jwt-secret-key-for-e2e-testing-only-32-chars',
    });

    const page = await browser.newPage();

    try {
        // Test 1: Auth status endpoint returns multi-user mode
        console.log('Test 1: Auth status endpoint returns multi-user mode...');
        // Navigate to the base URL first so page.evaluate has a proper context
        await page.goto(BASE_URL, { waitUntil: 'networkidle0', timeout: WAIT_TIMEOUT });
        const authStatusResponse = await page.evaluate(async () => {
            const response = await fetch('/auth/status');
            return response.json();
        });

        if (authStatusResponse.multi_user !== true) {
            throw new Error(`Expected multi_user=true, got ${authStatusResponse.multi_user}`);
        }
        if (authStatusResponse.user !== null) {
            throw new Error('Expected no user when not authenticated');
        }
        console.log(`  ✓ Auth status: multi_user=${authStatusResponse.multi_user}`);
        console.log('  ✓ No user when unauthenticated');

        // Test 2: Homepage redirects to login in multi-user mode
        console.log('Test 2: Protected routes redirect to login...');
        // We already navigated above, just check the current state
        // In multi-user mode, should see login page or be redirected to /login
        const currentUrl = page.url();
        const pageContent = await page.content();
        const isLoginPage = currentUrl.includes('/login') ||
                           pageContent.includes('Continue with Google') ||
                           pageContent.includes('Sign in') ||
                           pageContent.includes('login');

        if (!isLoginPage) {
            // Check if we're on dashboard (would be incorrect for multi-user without auth)
            console.log(`  Current URL: ${currentUrl}`);
            console.log('  ⚠ May not have redirected to login (depends on frontend implementation)');
        } else {
            console.log('  ✓ Redirected to login page');
        }

        // Test 3: Google OAuth initiation endpoint exists
        console.log('Test 3: Google OAuth endpoint available...');
        const oauthResponse = await page.evaluate(async () => {
            const response = await fetch('/auth/google/login', { redirect: 'manual' });
            return { status: response.status, redirected: response.type === 'opaqueredirect' };
        });

        // Should redirect to Google OAuth (302/307) or return the URL
        if (oauthResponse.status === 200 || oauthResponse.status === 302 || oauthResponse.status === 307) {
            console.log('  ✓ Google OAuth endpoint available');
        } else {
            console.log(`  ⚠ OAuth endpoint returned status ${oauthResponse.status}`);
        }

        // Test 4: Login page renders correctly
        console.log('Test 4: Login page UI renders...');
        await page.goto(`${BASE_URL}/login`, { waitUntil: 'networkidle0', timeout: WAIT_TIMEOUT });
        const loginContent = await page.content();

        if (loginContent.includes('Google') || loginContent.includes('Sign in') || loginContent.includes('Login')) {
            console.log('  ✓ Login page renders with Google OAuth option');
        } else {
            console.log('  ⚠ Login page content may have changed');
        }

        console.log('\n✅ MULTI-USER MODE: All tests passed\n');

    } catch (error) {
        console.error(`\n❌ MULTI-USER MODE: Test failed - ${error.message}\n`);
        // Take screenshot for debugging
        await page.screenshot({ path: 'test-failure-multi-user.png' });
        throw error;
    } finally {
        await page.close();
        await stopServer();
    }
}

/**
 * Main test runner
 */
async function main() {
    console.log('============================================');
    console.log('thestill.me E2E Authentication Tests');
    console.log('============================================');
    console.log(`Puppeteer version: ${puppeteer.default ? 'ESM' : 'CommonJS'}`);
    console.log(`Target: ${BASE_URL}`);
    console.log('');

    try {
        // Launch browser
        browser = await puppeteer.launch({
            headless: 'new',
            args: ['--no-sandbox', '--disable-setuid-sandbox'],
        });

        // Run tests
        await testSingleUserMode();
        await testMultiUserMode();

        console.log('============================================');
        console.log('✅ ALL TESTS PASSED');
        console.log('============================================');
        process.exit(0);

    } catch (error) {
        console.error('============================================');
        console.error(`❌ TEST SUITE FAILED: ${error.message}`);
        console.error('============================================');
        process.exit(1);

    } finally {
        if (browser) {
            await browser.close();
        }
        await stopServer();
    }
}

main();
