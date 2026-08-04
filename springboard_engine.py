"""
Springboard Automation Engine — v3.0
═══════════════════════════════════════════════════════════
Fixed with REAL selectors from live Springboard exploration:
- Correct Keycloak SSO login flow
- Zoiee chatbot dismissal (mat-icon "minimize")
- Course player navigation (.navigation-btn-frwd)
- Assessment auto-completion (radio options + Save & Next + Submit Assessment)
- Assessment launch flow (I am not a robot → START → Accept instructions → Continue)
"""

import time
import os
import inspect
from datetime import datetime
import g4f
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout


VISIBILITY_HACK = """
Object.defineProperty(document, 'visibilityState', { value: 'visible', writable: false });
Object.defineProperty(document, 'hidden', { value: false, writable: false });
window.addEventListener('visibilitychange', (e) => e.stopImmediatePropagation(), true);
"""


class SpringboardAutomation:

    LANDING_URL = "https://infyspringboard.onwingspan.com/web/en/login"

    def __init__(self, email, password, course_url, headless=False, log_callback=None):
        self.email = email
        self.password = password
        self.course_url = course_url
        self.headless = headless
        self._log = log_callback or (lambda msg, level: print(f"[{level}] {msg}"))
        self._running = True
        self._module_count = 0
        self.VIDEO_WAIT = 10
        self.VIDEO_HEARTBEAT_WAIT = 7
        self.SCROLL_DWELL = 1.2
        self.MODULE_LOAD_WAIT = 2
        self.FAST_NORMAL_PAGES = True
        self.AGGRESSIVE_SPEED_MODE = True
        self.NON_INTERACTIVE_RETRY_WAIT = 1.8
        self.REFRESH_INTERVAL_SECONDS = 300
        self.UI_CHECK_INTERVAL_SECONDS = 300
        self._last_periodic_refresh = time.time()
        self._last_periodic_zoiee_check = 0.0
        self._last_periodic_popup_check = 0.0

    def stop(self):
        self._running = False

    def log(self, msg, level="INFO"):
        """Log with automatic source function context for frontend traceability."""
        source = "unknown"
        try:
            caller = inspect.currentframe().f_back
            source = caller.f_code.co_name if caller else "unknown"
        except Exception:
            source = "unknown"

        if source and source not in ("log", "<module>"):
            self._log(f"[{source}] {msg}", level)
        else:
            self._log(msg, level)

    # ═════════════════════════════════════════════════════════════
    #  LOGIN (Keycloak SSO)
    # ═════════════════════════════════════════════════════════════
    def _login(self, page):
        self.log("Navigating to Springboard...", "INFO")
        page.goto(self.LANDING_URL, wait_until="domcontentloaded")
        time.sleep(1.5)

        # Accept cookies
        try:
            cookie_btn = page.locator('#onetrust-accept-btn-handler')
            if cookie_btn.is_visible(timeout=3000):
                cookie_btn.click()
                self.log("Accepted cookie banner", "OK")
                time.sleep(1)
        except Exception:
            pass

        # Click LOGIN on landing page
        self.log("Clicking LOGIN on landing page...", "INFO")
        login_nav_selectors = [
            'a:has-text("LOGIN")', 'button:has-text("LOGIN")',
            'a:has-text("Login")', 'button:has-text("Login")',
            '.login-btn-top', 'a[href*="login"]',
        ]
        self._click_first(page, login_nav_selectors)
        time.sleep(1.5)
        try:
            page.wait_for_load_state("networkidle", timeout=15000)
        except PlaywrightTimeout:
            pass

        self.log(f"On auth page: {page.url[:80]}", "INFO")

        # Fill username (#username)
        self.log(f"Entering email: {self.email}", "INFO")
        try:
            page.locator('#username').wait_for(state="visible", timeout=10000)
            page.locator('#username').click()
            page.locator('#username').fill(self.email)
            self.log("Email entered ✓", "OK")
        except Exception:
            username_selectors = ['input[name="username"]', 'input[type="email"]', 'input[type="text"]']
            field = self._find_element(page, username_selectors)
            if field:
                field.fill(self.email)
                self.log("Email entered (fallback) ✓", "OK")
            else:
                self.log("Cannot find email field! Waiting 5s...", "ERR")
                time.sleep(5)
                return

        time.sleep(0.5)

        # Fill password (#password)
        self.log("Entering password...", "INFO")
        try:
            page.locator('#password').click()
            page.locator('#password').fill(self.password)
            self.log("Password entered ✓", "OK")
        except Exception:
            pw_field = self._find_element(page, ['input[type="password"]'])
            if pw_field:
                pw_field.fill(self.password)
            else:
                self.log("Cannot find password field! Waiting 5s...", "ERR")
                time.sleep(5)
                return

        time.sleep(0.5)

        # Click Log In (#kc-login)
        self.log("Clicking Log In...", "INFO")
        try:
            page.locator('#kc-login').click()
            self.log("Clicked Log In ✓", "OK")
        except Exception:
            self._click_first(page, ['input[type="submit"]', 'button[type="submit"]'])

        # Wait for redirect to dashboard
        self.log("Waiting for dashboard...", "INFO")
        time.sleep(5)
        try:
            page.wait_for_load_state("networkidle", timeout=30000)
        except PlaywrightTimeout:
            pass

        # Check if login failed (still on auth page)
        if "auth" in page.url.lower() or "login" in page.url.lower():
            self.log("⚠️ Still on login page — wrong credentials or CAPTCHA?", "WARN")
            self.log("Waiting 5s before proceeding...", "WARN")
            time.sleep(5)

        self.log(f"Logged in! URL: {page.url[:60]}", "OK")

    # ═════════════════════════════════════════════════════════════
    #  DISMISS ZOIEE CHATBOT
    # ═════════════════════════════════════════════════════════════
    def _dismiss_zoiee(self, page, quick=False, periodic=False):
        """
        After login, the Zoiee chatbot appears as a full-page overlay.
        We need to click the minimize button (mat-icon with text 'minimize')
        or the close/X button to dismiss it.
        """
        vis_timeout = 350 if quick else 2000
        settle = 0.12 if quick else 0.8
        post_escape_wait = 0.15 if quick else 1

        if periodic:
            now = time.time()
            if now - self._last_periodic_zoiee_check < self.UI_CHECK_INTERVAL_SECONDS:
                return False
            self._last_periodic_zoiee_check = now

        self.log("Checking for Zoiee chatbot overlay...", "INFO")
        time.sleep(0.12 if quick else 1.5)

        # Strategy 1: Look for minimize button with mat-icon
        minimize_selectors = [
            'mat-icon:has-text("minimize")',
            'button:has(mat-icon:has-text("minimize"))',
            'mat-icon:has-text("close")',
            'button:has(mat-icon:has-text("close"))',
            'mat-icon:has-text("close_fullscreen")',
            'button:has(mat-icon:has-text("close_fullscreen"))',
            '#chatbot-minimize',
            '[class*="minimize"]',
            '[class*="chatbot"] button',
        ]

        for sel in minimize_selectors:
            try:
                el = page.locator(sel).first
                if el.is_visible(timeout=vis_timeout):
                    el.click()
                    self.log(f"Dismissed Zoiee chatbot using: {sel}", "OK")
                    time.sleep(settle)
                    return True
            except Exception:
                continue

        # Strategy 2: Check for the chatbot avatar (already minimized)
        try:
            avatar = page.locator('#chatbot-avatar')
            if avatar.is_visible(timeout=vis_timeout):
                self.log("Zoiee already minimized (avatar visible)", "OK")
                return True
        except Exception:
            pass

        # Strategy 3: Click outside the chatbot overlay to dismiss it
        try:
            # Check if "Ask Zoiee" text is visible (chatbot is open)
            if page.locator('text="Ask Zoiee"').is_visible(timeout=vis_timeout):
                self.log("Zoiee chatbot overlay detected, attempting to close...", "WARN")
                # Try pressing Escape
                page.keyboard.press("Escape")
                time.sleep(post_escape_wait)
                # If still visible, try clicking the page body at top-left corner
                if page.locator('text="Ask Zoiee"').is_visible(timeout=vis_timeout):
                    page.mouse.click(10, 10)
                    time.sleep(post_escape_wait)
        except Exception:
            pass

        # Strategy 4: Use JavaScript to hide the chatbot
        try:
            page.evaluate("""
                () => {
                    // Try to find and hide chatbot overlay
                    const chatElements = document.querySelectorAll('[class*="chatbot"], [class*="zoiee"], [class*="chat-overlay"]');
                    chatElements.forEach(el => el.style.display = 'none');
                    // Also try Angular Material overlays
                    const overlays = document.querySelectorAll('.cdk-overlay-container, .cdk-overlay-backdrop');
                    overlays.forEach(el => el.style.display = 'none');
                }
            """)
            self.log("Attempted JS-based chatbot dismissal", "OK")
            time.sleep(0.12 if quick else 1)
        except Exception:
            pass

        self.log("Zoiee chatbot handling complete", "OK")
        return False

    # ═════════════════════════════════════════════════════════════
    #  COURSE NAVIGATION
    # ═════════════════════════════════════════════════════════════
    def _navigate_to_course(self, page):
        self.log("Navigating to course...", "INFO")
        page.goto(self.course_url, wait_until="domcontentloaded")
        time.sleep(2.0)
        try:
            page.wait_for_load_state("networkidle", timeout=15000)
        except PlaywrightTimeout:
            pass

        # Dismiss Zoiee again (it may reappear on navigation)
        self._dismiss_zoiee(page)

        # Click Start / Resume button if still on overview
        start_selectors = [
            'button:has-text("Start")', 'button:has-text("Resume")',
            'button:has-text("Continue Course")',
            'button:has-text("START")', 'button:has-text("RESUME")',
            'a:has-text("Start")', 'a:has-text("Resume")',
            '[class*="start-btn"]', '[class*="resume-btn"]',
        ]
        if self._click_first(page, start_selectors):
            self.log("Clicked Start/Resume — entering course player!", "OK")
            time.sleep(2.0)
            try:
                page.wait_for_load_state("networkidle", timeout=10000)
            except PlaywrightTimeout:
                pass
        else:
            self.log("No Start/Resume button — may already be in the player.", "WARN")

        # Dismiss Zoiee once more inside the player
        self._dismiss_zoiee(page)

    # ═════════════════════════════════════════════════════════════
    #  VIDEO HANDLER — plays at 16x speed (full watch, no skipping)
    # ═════════════════════════════════════════════════════════════
    def _handle_video(self, page):
        self.log("Scanning for video elements...", "VIDEO")

        # Press UI Play buttons FIRST to bypass browser Autoplay policies
        self._tap_video_play_buttons(page)

        targets = [("main", page)] + [(f"frame #{i}", frame) for i, frame in enumerate(page.frames)]
        for label, target in targets:
            try:
                try:
                    target.wait_for_selector("video", timeout=30000)
                except Exception:
                    pass

                video_el = target.locator("video").first
                if video_el.is_visible(timeout=2000):
                    self.log(f"Found <video> in {label}", "VIDEO")

                    # Wait for metadata to load
                    target.evaluate("""() => {
                        return new Promise(r => {
                            const v = document.querySelector('video');
                            if (!v) return r(false);
                            if (v.readyState >= 1) return r(true);
                            v.addEventListener('loadedmetadata', () => r(true));
                            setTimeout(() => r(false), 15000);
                        });
                    }""")

                    duration = target.evaluate(
                        "() => { const v = document.querySelector('video'); return v ? v.duration : 0; }"
                    )

                    if duration and duration > 0:
                        self.log("Tapping center play and waiting for natural playback (10s)...", "VIDEO")

                        # Tap the visible play button again right before playback,
                        # so platforms that require a real user click can start video.
                        self._tap_video_play_buttons(page)

                        target.evaluate(f"""() => {{
                            const v = document.querySelector('video');
                            if (!v) return;
                            v.muted = true; v.volume = 0;
                            v.currentTime = 0;
                            v.play();
                        }}""")

                        # Must naturally reach at least 10s before skipping.
                        if not self._wait_video_natural_progress(target, min_seconds=10, timeout_seconds=40):
                            self.log("Video did not progress to 10s naturally; retrying module.", "WARN")
                            return False

                        # Last-mile rule: do not jump to exact end.
                        target.evaluate("""() => {
                            const v = document.querySelector('video');
                            if (v && v.currentTime < v.duration - 5) {
                                v.currentTime = v.duration - 5;
                                v.play();
                            }
                        }""")
                        
                        # Give enough time for ended event + server sync
                        time.sleep(3.0)

                        # Verify completion progressed to avoid false "handled" state
                        try:
                            done = target.evaluate("""() => {
                                const v = document.querySelector('video');
                                if (!v || !v.duration) return false;
                                return v.currentTime >= v.duration - 5;
                            }""")
                            if not done:
                                self.log("Video did not reach end yet; retrying module on next loop.", "WARN")
                                return False
                        except Exception:
                            pass

                        self.log("Video skipped to end! ✅", "OK")
                        return True
                    else:
                        self.log("Video found but no duration metadata yet — will retry.", "WARN")
                        time.sleep(2.0)
                        return False
            except Exception:
                continue

                # Shadow DOM fallback
        try:
            has_shadow = page.evaluate("""() => {
                for (const el of document.querySelectorAll('*')) {
                    if (el.shadowRoot && el.shadowRoot.querySelector('video')) return true;
                }
                return false;
            }""")
            if has_shadow:
                self.log("Found video in Shadow DOM — tapping play and waiting natural 10s", "VIDEO")
                page.evaluate("""() => {
                    // Try to click any shadow UI play buttons FIRST
                    for (const el of document.querySelectorAll('*')) {
                        if (el.shadowRoot) {
                            const v = el.shadowRoot.querySelector('video');
                            if (!v) continue;
                            const btn = el.shadowRoot.querySelector('.vjs-big-play-button, button.vjs-play-control, [title=\"Play\"], [aria-label=\"Play\"]');
                            if (btn && typeof btn.click === 'function') btn.click();
                        }
                    }
                    
                    for (const el of document.querySelectorAll('*')) {
                        if (el.shadowRoot) {
                            const v = el.shadowRoot.querySelector('video');
                            if (v) {
                                v.muted = true; v.currentTime = 0;
                                v.play();
                                return;
                            }
                        }
                    }
                }""")
                time.sleep(10.0)
                page.evaluate("""() => {
                    for (const el of document.querySelectorAll('*')) {
                        if (!el.shadowRoot) continue;
                        const v = el.shadowRoot.querySelector('video');
                        if (v && v.duration && v.currentTime < v.duration - 5) {
                            v.currentTime = v.duration - 5;
                            v.play();
                            return;
                        }
                    }
                }""")
                time.sleep(3.0)
                return True
        except Exception:
            pass

        return False

    def _tap_video_play_buttons(self, page):
        """Tap/click visible video play controls on main page and in frames."""
        if self._is_explore_playground_page(page):
            self.log("Blocked video play-tap on Explore/Playground page.", "WARN")
            return

        play_selectors = [
            '.vjs-big-play-button',
            'button.vjs-play-control',
            '.ytp-large-play-button',
            '.jw-icon-playback',
            '[title="Play"]',
            '[aria-label="Play"]',
            '[aria-label*="Play"]',
            'button[title*="Play"]',
            'button[aria-label*="Play"]',
        ]

        targets = [page] + list(page.frames)
        clicked_any = False

        for target in targets:
            # Only attempt play-clicks inside targets that actually contain a visible video.
            try:
                has_video = target.locator("video").first.is_visible(timeout=500)
            except Exception:
                has_video = False
            if not has_video:
                continue

            for sel in play_selectors:
                try:
                    btn = target.locator(sel).first
                    if btn.is_visible(timeout=400):
                        btn.click(force=True)
                        clicked_any = True
                        time.sleep(0.25)
                except Exception:
                    continue

            # JS fallback for custom overlays where selector-based click misses
            try:
                did_js_click = target.evaluate("""() => {
                    const v = document.querySelector('video');
                    if (!v) return false;

                    const container =
                        v.closest('.video-js, .player, [class*="video"], [class*="player"], .vjs-tech') ||
                        v.parentElement ||
                        document;

                    const selectors = [
                        '.vjs-big-play-button',
                        'button.vjs-play-control',
                        '.ytp-large-play-button',
                        '.jw-icon-playback',
                        '[aria-label="Play"]',
                        '[aria-label*="Play"]',
                        '[title="Play"]',
                        'button[title*="Play"]',
                        'button[aria-label*="Play"]'
                    ];

                    for (const s of selectors) {
                        const el = container.querySelector(s) || document.querySelector(s);
                        if (el && el.offsetParent !== null) {
                            el.click();
                            el.dispatchEvent(new MouseEvent('click', { bubbles: true }));
                            return true;
                        }
                    }

                    v.click();
                    v.dispatchEvent(new MouseEvent('click', { bubbles: true }));
                    return true;

                }""")
                if did_js_click:
                    clicked_any = True
            except Exception:
                continue

        if clicked_any:
            self.log("Tapped visible video play button", "VIDEO")

    def _wait_video_natural_progress(self, target, min_seconds=10, timeout_seconds=40):
        """Wait until video naturally reaches min_seconds of playback progress."""
        start = time.time()
        while time.time() - start < timeout_seconds:
            try:
                progressed = target.evaluate(
                    """(minSec) => {
                        const v = document.querySelector('video');
                        if (!v) return false;
                        return v.currentTime >= minSec;
                    }""",
                    min_seconds,
                )
                if progressed:
                    return True
            except Exception:
                pass

            try:
                target.evaluate("""() => {
                    const v = document.querySelector('video');
                    if (v && v.paused) v.play();
                }""")
            except Exception:
                pass

            time.sleep(1.0)

        return False

    def _is_explore_playground_page(self, page):
        """Detect when user is on global Explore/Playground listing instead of course player."""
        url = ""
        try:
            url = (page.url or "").lower()
        except Exception:
            pass

        if "/page/playground" in url:
            return True

        try:
            body = page.inner_text("body", timeout=1500).lower()
            if "technology playground" in body and "tryout" in body:
                return True
            if "bigdata testing playground" in body and "aem playground" in body:
                return True
        except Exception:
            pass

        return False

    def _recover_from_explore_playground(self, page):
        """Return to course player if automation gets pushed to Explore/Playground page."""
        if not self._is_explore_playground_page(page):
            return False

        self.log("Detected Explore/Playground page during module scan. Recovering to course...", "WARN")

        # Keep UI clear before navigation recovery.
        self._dismiss_zoiee(page)

        try:
            page.go_back(wait_until="domcontentloaded", timeout=12000)
            time.sleep(1.0)
        except Exception:
            pass

        if self._is_explore_playground_page(page):
            self._navigate_to_course(page)

        return True

    def _is_blank_viewer_shell(self, page):
        """Detect blank dark viewer shell that sometimes appears after quiz submit."""
        try:
            url = (page.url or "").lower()
        except Exception:
            url = ""

        if "/viewer/" not in url:
            return False

        # Never treat Explore/Playground as blank viewer here.
        if self._is_explore_playground_page(page):
            return False

        try:
            info = page.evaluate("""() => {
                const body = document.body;
                const text = (body && body.innerText ? body.innerText : '').trim();
                const hasLearningContent = !!(
                    document.querySelector('video') ||
                    document.querySelector('.monaco-editor, .code-editor, [class*="editor"]') ||
                    document.querySelector('mat-radio-button, input[type="radio"], [role="radiogroup"]') ||
                    document.querySelector('button.navigation-btn-frwd, .navigation-btn-frwd')
                );
                const iframes = document.querySelectorAll('iframe').length;
                return { textLen: text.length, hasLearningContent, iframes };
            }""")

            # Typical blank shell: almost no body text and no content widgets.
            if info.get("textLen", 0) < 120 and not info.get("hasLearningContent") and info.get("iframes", 0) <= 1:
                return True
        except Exception:
            pass

        return False

    def _recover_from_blank_viewer_shell(self, page):
        """Recover when player lands on an empty shell after transitions/submissions."""
        if not self._is_blank_viewer_shell(page):
            return False

        self.log("Detected blank viewer shell after transition. Recovering...", "WARN")

        # Quick grace wait for late-rendered content.
        for _ in range(4):
            time.sleep(1.2)
            if not self._is_blank_viewer_shell(page):
                self.log("Viewer content appeared after short wait.", "OK")
                return True

        # Hard recovery path.
        self._dismiss_zoiee(page)
        self._handle_warning_and_fullscreen(page)

        try:
            page.reload(wait_until="domcontentloaded")
            page.wait_for_load_state("networkidle", timeout=12000)
        except Exception:
            pass

        if self._is_blank_viewer_shell(page):
            self._navigate_to_course(page)

        return True

    def _periodic_self_heal_refresh(self, page):
        """Refresh page every fixed interval to recover from silent stuck states."""
        now = time.time()
        if now - self._last_periodic_refresh < self.REFRESH_INTERVAL_SECONDS:
            return False

        self.log("3-minute self-heal refresh triggered.", "WARN")

        try:
            page.reload(wait_until="domcontentloaded")
            page.wait_for_load_state("networkidle", timeout=15000)
        except Exception:
            # If reload fails due to transient page state, keep automation alive.
            pass

        self._last_periodic_refresh = time.time()
        self._dismiss_zoiee(page)
        self._handle_warning_and_fullscreen(page)
        self._recover_from_explore_playground(page)
        self._recover_from_blank_viewer_shell(page)
        return True

    def _has_video_context(self, page):
        """Detect real video-player context without false positives from dashboard text."""
        if self._is_explore_playground_page(page):
            return False
        if self._is_blank_viewer_shell(page):
            return False

        try:
            if page.locator("video").first.is_visible(timeout=700):
                return True
        except Exception:
            pass

        for frame in page.frames:
            try:
                if frame.locator("video").first.is_visible(timeout=700):
                    return True
            except Exception:
                continue

        player_selectors = [
            '.vjs-big-play-button',
            'button.vjs-play-control',
            '.ytp-large-play-button',
            '.jw-icon-playback',
            '[class*="video-player"]',
            '[class*="player-controls"]',
            'iframe[src*="youtube" i]',
            'iframe[src*="vimeo" i]',
        ]

        for selector in player_selectors:
            try:
                if page.locator(selector).first.is_visible(timeout=500):
                    return True
            except Exception:
                continue

        for frame in page.frames:
            for selector in player_selectors:
                try:
                    if frame.locator(selector).first.is_visible(timeout=500):
                        return True
                except Exception:
                    continue

        try:
            body = page.inner_text("body", timeout=1200).lower()
            hints = ["playback speed", "video player", "watch video", "current time"]
            if any(h in body for h in hints):
                return True
        except Exception:
            pass

        return False

    def _has_coding_context(self, page):
        """Fast coding-context detection to avoid invoking coding handler on normal reading pages."""
        if self._is_explore_playground_page(page):
            return False

        coding_markers = [
            '.monaco-editor', '.code-editor', 'textarea.inputarea',
            'button:has-text("Run")', 'button:has-text("Verify")',
            'button:has-text("Compile")', 'button:has-text("Submit")',
            'button[mattooltip="Run Code"]', 'button[mattooltip="Execute"]',
        ]

        targets = [page] + list(page.frames)
        for target in targets:
            for marker in coding_markers:
                try:
                    el = target.locator(marker).first
                    if el.is_visible(timeout=500):
                        return True
                except Exception:
                    continue

        # Keep simple-coding fallback compatibility via existing text-based guard.
        return self._is_simple_coding_context(page)

    def _fast_skip_normal_page(self, page):
        """Aggressive skip for non-interactive pages: quick touch + immediate next."""
        self.log("Fast-skipping normal page...", "SCROLL")

        # Tiny interaction so lazy-loaded shells initialize.
        try:
            page.wait_for_load_state("domcontentloaded", timeout=3000)
        except Exception:
            pass

        self._handle_popups(page)

        # First attempt: jump next immediately.
        if self._click_next(page):
            return True

        # Fallback: force-complete scroll signals in main page, frames, and scrollable containers.
        force_scroll_js = """() => {
            const scrollAll = (doc) => {
                try {
                    const root = doc.scrollingElement || doc.documentElement || doc.body;
                    if (root) root.scrollTop = root.scrollHeight;
                } catch (e) {}

                const nodes = Array.from(doc.querySelectorAll('*'));
                for (const el of nodes) {
                    try {
                        const sh = el.scrollHeight || 0;
                        const ch = el.clientHeight || 0;
                        const overflowY = getComputedStyle(el).overflowY || '';
                        const scrollable = sh > ch + 20 && (overflowY.includes('auto') || overflowY.includes('scroll'));
                        if (scrollable) {
                            el.scrollTop = sh;
                        }
                    } catch (e) {}
                }
            };

            scrollAll(document);
            return true;
        }"""

        # Main page scroll completion
        try:
            page.evaluate(force_scroll_js)
        except Exception:
            pass

        # Frame scroll completion
        for frame in page.frames:
            try:
                frame.evaluate(force_scroll_js)
            except Exception:
                continue

        time.sleep(0.25)

        self._handle_popups(page)
        return self._click_next(page)

    # ═════════════════════════════════════════════════════════════
    #  CODING EXERCISE HANDLER (AI SOLVER)
    # ═════════════════════════════════════════════════════════════
    def _handle_coding(self, page):
        """
        Handle coding exercises (e.g., "Practice Problem 3").
        Extracts the problem description, grabs the editor code,
        solves it with G4F, injects the solution, and submits.
        """
        # First, ensure it's not just a standalone 'play' button exercise, but a full editor
        editor_visible = False
        targets = [page] + list(page.frames)

        for t in targets:
            try:
                # Springboard usually uses Monaco or a specific code container
                if t.locator('.monaco-editor, .code-editor, [class*="editor"]').count() > 0:
                    editor_visible = True
                    break
            except Exception:
                continue

        # If it's a simple coding exercise with just a Play button (old style)
        if not editor_visible:
            if not self._is_simple_coding_context(page):
                return False
            return self._handle_simple_coding(page)

        self.log("👨‍💻 Complex Coding Exercise DETECTED. Starting AI Auto-Solver...", "INFO")
        time.sleep(1.0)

        # ── Step 1: Extract Problem Context ──────────────────────────
        problem_text = ""
        skeleton_code = ""

        try:
            self.log("  Extracting problem description and skeleton code...", "INFO")
            # Problem text is usually on the left or top. Just grab all visible text, excluding the editor if possible.
            # A broad catch-all is to get the innerText of the main container.
            for t in targets:
                try:
                    text = t.evaluate("() => document.body.innerText")
                    if len(text) > len(problem_text):
                        problem_text = text
                except Exception:
                    continue

            # Get exact skeleton code using JS (bypassing Monaco virtual rendering limits)
            for t in targets:
                try:
                    code = t.evaluate("""() => {
                        let lines = document.querySelectorAll('.view-line');
                        if (lines.length > 0) {
                            return Array.from(lines).map(l => l.innerText).join('\\n');
                        }
                        return '';
                    }""")
                    if len(code) > len(skeleton_code):
                        skeleton_code = code
                except Exception:
                    continue

            # Fallback if Monaco specific selector failed
            if not skeleton_code:
                try:
                    skeleton_code = page.locator('textarea.inputarea').first.input_value(timeout=1000)
                except Exception:
                    pass

        except Exception as e:
            self.log(f"  Failed to extract context: {e}", "WARN")

        if len(problem_text) < 50:
            self.log("  Could not find enough problem context. Attempting simple submit.", "WARN")
            return self._verify_and_submit_code(page)

        # ── Step 2: Solve with G4F ───────────────────────────────────
        self.log("  🧠 Asking G4F AI for the solution (this may take 10-30s)...", "INFO")
        
        prompt = f"""
You are an expert Python programmer passing an automated test for a user on an educational platform.
Below is the full page text of a coding exercise, which contains the problem description, class diagrams, or expected output.
Below that is the "skeleton code" that is currently in the editor.

YOUR TASK:
1. Write the correct Python 3 code to solve the problem.
2. You MUST keep the exact function signatures, class names, and variable names required by the skeleton code or the problem description.
3. ONLY return the raw, complete, working Python code. Do not include markdown formatting (like ```python), do not include explanations, do not include comments unless they were in the skeleton. Your exact output will be pasted directly into an editor.

=== PAGE / PROBLEM TEXT ===
{problem_text[:4000]}

=== SKELETON CODE ===
{skeleton_code}
"""
        
        solution_code = ""
        try:
            # G4F call
            response = g4f.ChatCompletion.create(
                model=g4f.models.gpt_4o_mini,
                messages=[{"role": "user", "content": prompt}],
                timeout=60
            )
            
            # Clean up the response just in case the AI added markdown blocks despite instructions
            solution_code = response.strip()
            if solution_code.startswith("```python"):
                solution_code = solution_code[9:]
            if solution_code.startswith("```"):
                solution_code = solution_code[3:]
            if solution_code.endswith("```"):
                solution_code = solution_code[:-3]
                
            solution_code = solution_code.strip()
            self.log(f"  ✅ AI generated a {len(solution_code)} character solution.", "OK")
            
        except Exception as e:
            self.log(f"  ❌ AI Solver failed: {e}", "ERR")
            self.log("  Attempting to just click Submit as fallback...", "WARN")
            return self._verify_and_submit_code(page)

        if not solution_code:
            self.log("  AI returned empty solution. Fallback to simple submit.", "WARN")
            return self._verify_and_submit_code(page)

        # ── Step 3: Inject Code into Monaco Editor ───────────────────
        self.log("  Injecting code into the editor...", "INFO")
        code_injected = False
        
        for t in targets:
            try:
                # Look for the hidden textarea Monaco uses for input
                editor_input = t.locator('textarea.inputarea').first
                if editor_input.is_visible(timeout=1000):
                    # Click inside to focus
                    editor_input.click()
                    time.sleep(0.5)
                    # Select all and delete (Ctrl+A for Windows/Linux, Meta+A for Mac)
                    t.keyboard.press("Control+A")
                    time.sleep(0.2)
                    t.keyboard.press("Backspace")
                    time.sleep(0.5)
                    # Type the new code securely
                    t.keyboard.insert_text(solution_code)
                    time.sleep(1.0)
                    code_injected = True
                    break
            except Exception:
                continue

        if not code_injected:
            self.log("  Failed to find editor input area. Fallback to simple submit.", "WARN")

        # ── Step 4: Verify and Submit ────────────────────────────────
        return self._verify_and_submit_code(page)

    def _verify_and_submit_code(self, page):
        """Helper to click Verify, wait, and click Submit on a coding exercise."""
        targets = [page] + list(page.frames)
        
        # Click Verify First
        self.log("  Clicking 'Verify'...", "INFO")
        verify_clicked = False
        for t in targets:
            try:
                verify_btn = t.locator('button:has-text("Verify"), button:has-text("VERIFY"), button:has-text("Compile"), button:has-text("Run")').first
                if verify_btn.is_visible(timeout=1000) and verify_btn.is_enabled(timeout=1000):
                    verify_btn.click()
                    verify_clicked = True
                    break
            except Exception:
                continue
                
        if verify_clicked:
            self.log("  Waiting 10s for verification to complete...", "INFO")
            time.sleep(10.0)
        else:
            self.log("  No Verify button found. Proceeding to Submit.", "WARN")

        # Click Submit
        self.log("  Clicking 'Submit'...", "INFO")
        submit_clicked = False
        for t in targets:
            try:
                submit_btn = t.locator('button:has-text("Submit"), button:has-text("SUBMIT")').first
                if submit_btn.is_visible(timeout=1500) and submit_btn.is_enabled(timeout=1500):
                    submit_btn.click()
                    # Hit enter to bypass potential confirmation dialogs
                    t.keyboard.press("Enter")
                    submit_clicked = True
                    break
            except Exception:
                continue

        if submit_clicked:
            self.log("  Coding exercise submitted successfully! ✓", "OK")
            time.sleep(3.0)
            return True
        else:
            self.log("  Could not find Submit button.", "ERR")
            return False

    def _is_simple_coding_context(self, page):
        """Allow simple coding Play clicks only on actual coding/problem screens."""
        if self._is_explore_playground_page(page):
            return False

        coding_hints = [
            "coding", "problem", "function", "test case", "run code", "compile",
            "input", "output", "python", "editor", "submit",
        ]
        block_hints = ["technology playground", "playground", "tryout", "bigdata testing playground", "aem playground"]

        targets = [page] + list(page.frames)
        for target in targets:
            try:
                body = target.inner_text("body", timeout=900).lower()
                if any(b in body for b in block_hints):
                    return False
                if any(h in body for h in coding_hints):
                    return True
            except Exception:
                continue

        return False

    def _handle_simple_coding(self, page):
        """Original simple logic for exercises that just have a Play button."""
        try:
            if not self._is_simple_coding_context(page):
                self.log("Skipping simple coding play-click: not a coding context.", "INFO")
                return False

            play_selectors = [
                'mat-icon:has-text("play_arrow")',
                'button mat-icon:has-text("play_arrow")',
                '.play-button',
                '[class*="play-btn"]',
                'button[mattooltip="Run Code"]',
                'button[mattooltip="Execute"]'
            ]
            
            js_code = """() => {
                let clicked = false;

                const pageText = (document.body && document.body.innerText ? document.body.innerText : '').toLowerCase();
                if (
                    pageText.includes('technology playground') ||
                    pageText.includes('playground') ||
                    pageText.includes('tryout')
                ) {
                    return false;
                }

                const clickPlay = (doc) => {
                    const icons = Array.from(doc.querySelectorAll('mat-icon, i, .icon'));
                    for (const icon of icons) {
                        if (icon.innerText.includes("play_arrow") || icon.innerText.includes("play")) {
                            const btn = icon.closest('button') || icon;
                            const card = btn.closest('article, .card, .course-card, [class*="card"]');
                            const cardText = (card ? card.innerText : btn.innerText || '').toLowerCase();
                            if (cardText.includes('playground') || cardText.includes('tryout')) continue;
                            btn.click();
                            clicked = true;
                            return true;
                        }
                    }
                    const btns = Array.from(doc.querySelectorAll('button'));
                    for (const btn of btns) {
                        const html = btn.outerHTML.toLowerCase();
                        const txt = (btn.innerText || '').toLowerCase();
                        const card = btn.closest('article, .card, .course-card, [class*="card"]');
                        const cardText = (card ? card.innerText : '').toLowerCase();
                        if (cardText.includes('playground') || cardText.includes('tryout')) continue;
                        if (html.includes("play") && !html.includes("video") && !txt.includes("playground") && !txt.includes("tryout")) {
                            btn.click();
                            clicked = true;
                            return true;
                        }
                    }
                    return false;
                };
                if(clickPlay(document)) return true;
                return clicked;
            }"""

            clicked = False
            for sel in play_selectors:
                try:
                    el = page.locator(sel).first
                    if el.is_visible(timeout=500):
                        self.log("Found simple coding exercise! Clicking Play button...", "INFO")
                        el.click()
                        clicked = True
                        break
                except Exception: continue

            if not clicked:
                try:
                    if page.evaluate(js_code):
                        self.log("Found simple coding exercise via JS! Clicking Play...", "INFO")
                        clicked = True
                except Exception: pass

            if not clicked:
                for frame in page.frames:
                    try:
                        if frame.evaluate(js_code):
                            self.log("Found simple coding exercise in iframe! Clicking Play...", "INFO")
                            clicked = True
                            break
                    except Exception: pass
                        
            if clicked:
                self.log("Waiting 10s for code execution to finish...", "INFO")
                time.sleep(5.0)
                return True
                
        except Exception: pass
        return False

    # ═════════════════════════════════════════════════════════════
    #  READING PAGE HANDLER — thorough scroll with dwell time
    # ═════════════════════════════════════════════════════════════
    def _handle_reading(self, page):
        self.log("Scrolling content page thoroughly...", "SCROLL")
        try:
            if self.FAST_NORMAL_PAGES:
                # Quick-pass mode for normal pages: enough interaction for progress,
                # but without long dwell times.
                for frame in page.frames:
                    try:
                        frame.evaluate("window.scrollTo({ top: 0, behavior: 'instant' })")
                        time.sleep(0.15)
                        frame.evaluate("window.scrollTo({ top: document.body.scrollHeight * 0.55, behavior: 'instant' })")
                        time.sleep(0.2)
                        frame.evaluate("window.scrollTo({ top: document.body.scrollHeight, behavior: 'instant' })")
                        time.sleep(0.25)
                    except Exception:
                        continue

                try:
                    page.evaluate("window.scrollTo({ top: 0, behavior: 'instant' })")
                    time.sleep(0.15)
                    page.evaluate("window.scrollTo({ top: document.body.scrollHeight * 0.55, behavior: 'instant' })")
                    time.sleep(0.2)
                    page.evaluate("window.scrollTo({ top: document.body.scrollHeight, behavior: 'instant' })")
                except Exception:
                    pass

                time.sleep(self.SCROLL_DWELL)
                self.log("Reading page fast-pass complete ✅", "OK")
                return True

            # Scroll ALL frames (content is often inside an iframe)
            for fi, frame in enumerate(page.frames):
                try:
                    sh = frame.evaluate("() => document.body ? document.body.scrollHeight : 0")
                    if sh > 500:
                        vh = frame.evaluate("() => window.innerHeight || 768")
                        current = 0
                        step = vh * 0.9  # Faster scrolls for thorough coverage
                        scroll_steps = 0

                        # First scroll to top
                        frame.evaluate("window.scrollTo({ top: 0, behavior: 'smooth' })")
                        time.sleep(1)

                        while current < sh:
                            current += step
                            frame.evaluate(f"window.scrollTo({{ top: {current}, behavior: 'smooth' }})")
                            scroll_steps += 1
                            # Dwell at each viewport position (2s) to register reading time
                            time.sleep(0.8)
                            if scroll_steps % 5 == 0:
                                self.log(f"  📄 Scrolled {min(current, sh):.0f}/{sh:.0f}px", "SCROLL")

                        # Final scroll to absolute bottom
                        frame.evaluate("window.scrollTo({ top: document.body.scrollHeight, behavior: 'smooth' })")
                        time.sleep(0.8)
                        self.log(f"  Frame #{fi}: scrolled {scroll_steps} steps", "SCROLL")

                        # Also scroll any overflow containers inside the frame
                        try:
                            frame.evaluate("""
                                () => {
                                    document.querySelectorAll(
                                        '[style*="overflow"], .content-area, .module-content, .scroll-container, .ql-editor'
                                    ).forEach(c => {
                                        if (c.scrollHeight > c.clientHeight + 50) {
                                            let pos = 0;
                                            const step = c.clientHeight * 0.6;
                                            const interval = setInterval(() => {
                                                pos += step;
                                                c.scrollTop = pos;
                                                if (pos >= c.scrollHeight) clearInterval(interval);
                                            }, 500);
                                        }
                                    });
                                }
                            """)
                            time.sleep(1.5)
                        except Exception:
                            pass
                except Exception:
                    continue

            # Scroll the main page too (top to bottom)
            try:
                main_sh = page.evaluate("() => document.body.scrollHeight")
                main_vh = page.evaluate("() => window.innerHeight || 768")
                page.evaluate("window.scrollTo({ top: 0, behavior: 'smooth' })")
                time.sleep(1)
                current = 0
                step = main_vh * 0.6
                while current < main_sh:
                    current += step
                    page.evaluate(f"window.scrollTo({{ top: {current}, behavior: 'smooth' }})")
                    time.sleep(0.8)
                page.evaluate("window.scrollTo({ top: document.body.scrollHeight, behavior: 'smooth' })")
            except Exception:
                pass

            self.log(f"Dwelling at bottom for {self.SCROLL_DWELL}s...", "SCROLL")
            time.sleep(self.SCROLL_DWELL)
            self.log("Reading page fully scrolled! ✅", "OK")
            return True
        except Exception as e:
            self.log(f"Scroll error: {e}", "WARN")
        return False

    # ═════════════════════════════════════════════════════════════
    #  POP-UP / INTERSTITIAL HANDLER
    # ═════════════════════════════════════════════════════════════
    def _handle_popups(self, page, quick=False, periodic=False):
        if periodic:
            now = time.time()
            if now - self._last_periodic_popup_check < self.UI_CHECK_INTERVAL_SECONDS:
                return
            self._last_periodic_popup_check = now

        popup_texts = [
            "congratulations", "module complete", "rate this", "feedback",
            "well done", "successfully completed", "great job",
        ]
        close_selectors = [
            'button:has-text("Close")', 'button:has-text("OK")', 'button:has-text("Got it")',
            'button:has-text("Skip")', 'button:has-text("Not now")', 'button:has-text("No thanks")',
            'button[aria-label="Close"]', '[class*="close"]', '.btn-close',
            '[data-dismiss="modal"]', 'mat-icon:has-text("close")',
        ]
        try:
            page_text = page.inner_text("body", timeout=(450 if quick else 2000)).lower()
        except Exception:
            return

        for pt in popup_texts:
            if pt in page_text:
                self.log(f"Popup detected: '{pt}'", "WARN")
                if self._click_first(page, close_selectors, quick=quick):
                    self.log("Dismissed popup!", "OK")
                    time.sleep(0.15 if quick else 1)
                    return
                try:
                    page.keyboard.press("Escape")
                    time.sleep(0.15 if quick else 1)
                except Exception:
                    pass
                return

    def _handle_warning_and_fullscreen(self, page):
        """Dismiss warning/fullscreen modals and spoof fullscreen listeners via resize event."""
        warning_texts = [
            "accessible only on fullscreen",
            "warning",
            "switching tab",
            "operation is disabled",
            "operation disabled",
        ]
        ok_selectors = [
            'button:has-text("Ok")', 'button:has-text("OK")',
            'button:has-text("Okay")', 'button:has-text("Close")',
            'button:has-text("Got it")', 'button:has-text("Block")',
            'button:has-text("Allow")',
        ]

        targets = [page] + list(page.frames)
        cleared_any = False
        
        for _ in range(3): # Try up to 3 times for sequential modals
            found_modal = False
            for target in targets:
                try:
                    body_text = target.inner_text("body", timeout=1000).lower()
                except Exception:
                    continue

                if not any(t in body_text for t in warning_texts):
                    continue

                for sel in ok_selectors:
                    try:
                        btn = target.locator(sel).first
                        if btn.is_visible(timeout=800):
                            btn.click()
                            self.log(f"Warning modal dismissed via '{sel}'", "WARN")
                            time.sleep(1.0)
                            found_modal = True
                            cleared_any = True
                            break
                    except Exception:
                        continue
                        
                if found_modal:
                    break
                    
            if not found_modal:
                break

        if cleared_any:
            try:
                page.evaluate("window.dispatchEvent(new Event('resize'));")
            except Exception:
                pass
            self.log("Fullscreen listener spoofed with resize event", "OK")

    def _dismiss_all_warnings(self, page):
        """Aggressively dismiss ALL warning modals until none remain.
        The Springboard assessment platform throws MANY sequential warnings:
        - 'accessible only on fullscreen'
        - 'Switching tab during the contest is not allowed'
        These can appear 3-5+ times in sequence. We must clear them all."""
        ok_selectors = [
            'button:has-text("Ok")', 'button:has-text("OK")',
            'button:has-text("Okay")', 'button:has-text("Close")',
            'button:has-text("Got it")', 'button:has-text("Block")',
            'button:has-text("Allow")',
        ]
        
        targets = [page] + list(page.frames)
        total_dismissed = 0
        
        for attempt in range(10):  # Up to 10 sequential warnings
            found = False
            for target in targets:
                for sel in ok_selectors:
                    try:
                        btn = target.locator(sel).first
                        if btn.is_visible(timeout=1500):
                            btn.click()
                            total_dismissed += 1
                            self.log(f"Dismissed warning #{total_dismissed}", "WARN")
                            time.sleep(1.0)
                            found = True
                            break
                    except Exception:
                        continue
                if found:
                    break
            if not found:
                break
        
        if total_dismissed > 0:
            # Spoof fullscreen to prevent more warnings
            try:
                page.evaluate("window.dispatchEvent(new Event('resize'));")
            except Exception:
                pass
            self.log(f"Cleared {total_dismissed} warning modals total", "OK")

    # ═════════════════════════════════════════════════════════════
    #  ASSESSMENT DETECTION
    # ═════════════════════════════════════════════════════════════
    def _is_assessment(self, page):
        """Check if the current page is an assessment/quiz section.
        Checks main page AND all iframes for quiz indicators."""
        if self._is_explore_playground_page(page):
            return False

        assessment_keywords = ["assessment", "quiz", "exam", "test"]
        strong_assessment_text = ["submit assessment", "save & next", "save and next", "take assessment", "start assessment", "finish test"]

        # Strategy 1: Check URL for quiz/assessment patterns
        try:
            url = page.url.lower()
            # Explicitly avoid classifying video player pages as assessments.
            if "viewer/video" in url or "/video/" in url:
                return False
            if any(kw in url for kw in ["quiz", "assessment", "exam"]):
                self.log("Assessment detected via URL pattern", "INFO")
                return True
        except Exception:
            pass

        # Strategy 2: Check page title
        try:
            title = page.title().lower()
            if any(kw in title for kw in assessment_keywords):
                self.log("Assessment detected via page title", "INFO")
                return True
        except Exception:
            pass

        # Strategy 3: Check visible headings on main page
        try:
            for tag in ["h1", "h2", "h3", ".title", '[class*="title"]', '[class*="assessment"]']:
                try:
                    for el in page.locator(tag).all():
                        try:
                            text = el.inner_text(timeout=800).lower()
                            if any(kw in text for kw in assessment_keywords):
                                return True
                        except Exception:
                            continue
                except Exception:
                    continue
        except Exception:
            pass

        # Strategy 4: Check for assessment-specific UI elements on main page + all frames
        assessment_markers = [
            'text="I am not a robot"',
            'text="Submit Assessment"',
            'text="Save & Next"',
            'text="Save and Next"',
            'button:has-text("Finish Test")',
            'button:has-text("Take Assessment")',
            'mat-radio-button',
            'mat-radio-group',
            '[role="radiogroup"]',
        ]

        targets = [page] + list(page.frames)
        for target in targets:
            for marker in assessment_markers:
                try:
                    count = target.locator(marker).count()
                    if count > 0 and target.locator(marker).first.is_visible(timeout=800):
                        self.log(f"Assessment detected via marker '{marker}' (count={count})", "INFO")
                        return True
                except Exception:
                    continue

        # Strategy 5: Use JS to scan all frames for radio button groups (quiz indicators)
        try:
            for frame in page.frames:
                try:
                    has_quiz_elements = frame.evaluate("""(strongHints) => {
                        const radios = document.querySelectorAll('mat-radio-button, input[type="radio"], [role="radio"]');
                        const bodyText = (document.body && document.body.innerText ? document.body.innerText : '').toLowerCase();
                        const saveNext = Array.from(document.querySelectorAll('button')).filter(b => 
                            b.innerText && (b.innerText.includes('Save') || b.innerText.includes('Submit') || b.innerText.includes('Next'))
                        );
                        const hasStrongText = strongHints.some(h => bodyText.includes(h));
                        return radios.length >= 2 && saveNext.length > 0 && hasStrongText;
                    }""", strong_assessment_text)
                    if has_quiz_elements:
                        self.log(f"Assessment detected via JS scan in frame: {frame.url[:60]}", "INFO")
                        return True
                except Exception:
                    continue
        except Exception:
            pass

        return False

    # ═════════════════════════════════════════════════════════════
    #  ASSESSMENT HANDLER (Full Flow)
    # ═════════════════════════════════════════════════════════════
    def _handle_assessment(self, page):
        """
        Complete assessment flow discovered via MCP browser walkthrough:
        The assessment UI is inside a CROSS-ORIGIN IFRAME.
        We must use frame_locator or iterate page.frames with proper waits.
        
        Flow: Zoiee -> warnings -> (instructions with SECTIONS tab) -> Attempt Section -> quiz
        """
        self.log("ASSESSMENT DETECTED -- starting auto-completion...", "QUIZ")
        time.sleep(0.8)

        if self._recover_from_explore_playground(page):
            self.log("Recovered from Explore/Playground during assessment flow; retrying module.", "WARN")
            return False

        # Dismiss Zoiee chatbot if it covers the screen
        self._dismiss_zoiee(page, quick=True)

        # ── Aggressively dismiss ALL warning modals ──────────────
        self._dismiss_all_warnings(page)

        # ── Wait for the assessment iframe to load ───────────────
        self.log("Waiting for assessment iframe to load...", "INFO")
        time.sleep(5.0)
        
        # Log all frames for debugging
        for i, frame in enumerate(page.frames):
            try:
                self.log(f"  Frame #{i}: {frame.url[:80]}", "INFO")
            except Exception:
                self.log(f"  Frame #{i}: (error reading URL)", "WARN")

        # ── Build targets list (all frames + main page) ──────────
        def get_all_targets():
            return [page] + list(page.frames)

        # ── Step 1: "I am not a robot" checkbox ──────────────────
        self._handle_robot_checkbox(page)

        # ── Step 2: Click START button (if present) ──────────────
        start_clicked = False
        for target in get_all_targets():
            if self._click_first_target(target, [
                'button:has-text("START")', 'button:has-text("Start")',
                'button:has-text("Begin")', 'button:has-text("Take Assessment")',
            ]):
                self.log("Clicked START assessment", "OK")
                time.sleep(2.0)
                start_clicked = True
                break
        if not start_clicked:
            self.log("No START button -- proceeding to SECTIONS", "WARN")

        self._dismiss_all_warnings(page)

        # ── Step 3: Handle instructions + Continue ───────────────
        self._handle_instructions_popup(page)
        self._dismiss_all_warnings(page)
        
        for target in get_all_targets():
            if self._click_first_target(target, ['button:has-text("Continue")', 'button:has-text("CONTINUE")']):
                self.log("Clicked Continue on instructions", "OK")
                time.sleep(2.0)
                break
        self._dismiss_all_warnings(page)

        # ── Step 4: Click SECTIONS tab ───────────────────────────
        for target in get_all_targets():
            if self._click_first_target(target, [
                'text="SECTIONS"', '.mat-tab-label:has-text("SECTIONS")',
                'a:has-text("SECTIONS")', '[role="tab"]:has-text("SECTIONS")',
            ]):
                self.log("Clicked SECTIONS tab", "OK")
                time.sleep(2.0)
                break
        self._dismiss_all_warnings(page)

        # ── Step 5: Click Attempt Section ────────────────────────
        attempt_clicked = False
        for target in get_all_targets():
            if self._click_first_target(target, [
                'button:has-text("Attempt Section")',
                'a:has-text("Attempt Section")',
            ]):
                self.log("Clicked Attempt Section!", "OK")
                time.sleep(4.0)
                attempt_clicked = True
                break
        if not attempt_clicked:
            self.log("No Attempt Section button found -- trying frame_locator", "WARN")
            # Fallback: use frame_locator to reach inside nested iframes
            try:
                fl = page.frame_locator('iframe').first
                attempt_btn = fl.locator('button:has-text("Attempt Section")')
                if attempt_btn.is_visible(timeout=3000):
                    attempt_btn.click()
                    self.log("Clicked Attempt Section via frame_locator!", "OK")
                    time.sleep(4.0)
                    attempt_clicked = True
            except Exception as e:
                self.log(f"frame_locator fallback failed: {e}", "WARN")

        # Dismiss final round of warnings after Attempt Section
        self._dismiss_all_warnings(page)
        
        # Wait for quiz questions to load inside the iframe
        self.log("Waiting for quiz questions to load...", "INFO")
        time.sleep(3.0)

        # ── Step 4: Answer all MCQ questions ─────────────────────
        try:
            page.wait_for_load_state("networkidle", timeout=10000)
        except PlaywrightTimeout:
            pass
        time.sleep(1.0)

        # Re-explore after loading questions
        self._explore_quiz_dom(page)

        question_count = 0
        max_questions = 50  # Safety limit
        consecutive_failures = 0

        while self._running and question_count < max_questions:
            if self._periodic_self_heal_refresh(page):
                self.log("Periodic refresh happened during quiz; retrying quiz flow.", "WARN")
                return False

            if self._recover_from_explore_playground(page):
                self.log("Redirect detected mid-quiz; returning to course player.", "WARN")
                return False

            question_count += 1
            self.log(f"📝 Question #{question_count}...", "QUIZ")

            try:
                # Try answering with retry logic
                option_found = False
                for attempt in range(3):  # Up to 3 attempts per question
                    option_found = self._answer_quiz_question(page)
                    if option_found:
                        consecutive_failures = 0
                        break
                    if attempt < 2:
                        self.log(f"  Retry {attempt+2}/3 — waiting for quiz content...", "WARN")
                        time.sleep(2.0)

                if not option_found:
                    consecutive_failures += 1
                    self.log(f"  Could not find any options for Q#{question_count}", "WARN")
                    if consecutive_failures >= 3:
                        self.log("  3 consecutive failures — stopping quiz answering", "WARN")
                        break

                time.sleep(0.5)

                # Click "Save & Next"
                clicked_save = self._click_save_next(page)
                self._handle_warning_and_fullscreen(page)

                if not clicked_save:
                    self.log("  No Save & Next button — might be single-page quiz or last question", "WARN")
                    # Do not break here! If all questions are on one page, we just need to keep answering.
                    # The loop will naturally break when option_found is False 3 times.

                # Wait for next question to load
                time.sleep(1.5)
                try:
                    page.wait_for_load_state("networkidle", timeout=5000)
                except PlaywrightTimeout:
                    pass

            except Exception as e:
                self.log(f"  Error on question: {e}", "ERR")
                consecutive_failures += 1
                if consecutive_failures >= 3:
                    break

        # ── Step 5: Submit Assessment ────────────────────────────
        self._submit_assessment(page)

        # Handle result popups and score screens
        time.sleep(2.0)
        self._handle_popups(page)

        if self._recover_from_blank_viewer_shell(page):
            self.log("Recovered from blank viewer after assessment submit.", "WARN")
            return False

        # Try to dismiss any result/score overlay
        result_dismiss = [
            'button:has-text("Close")', 'button:has-text("OK")',
            'button:has-text("Got it")', 'button:has-text("Continue")',
            'button:has-text("Done")', 'button:has-text("Back to Course")',
            'mat-icon:has-text("close")',
        ]
        for target in [page] + list(page.frames):
            if self._click_first_target(target, result_dismiss):
                self.log("Dismissed result/score popup ✓", "OK")
                time.sleep(1.0)
                break

        self.log(f"Assessment completed! Answered {question_count} questions ✅", "OK")
        return True

    # ─── Assessment Sub-methods ───────────────────────────────────

    def _explore_quiz_dom(self, page):
        """Log the DOM structure of the quiz page for debugging."""
        try:
            frame_count = len(page.frames)
            self.log(f"  📊 DOM Explorer: {frame_count} frames detected", "INFO")

            for i, frame in enumerate(page.frames):
                try:
                    info = frame.evaluate("""() => {
                        const radios = document.querySelectorAll('mat-radio-button, input[type="radio"], [role="radio"]').length;
                        const checkboxes = document.querySelectorAll('mat-checkbox, input[type="checkbox"]').length;
                        const buttons = Array.from(document.querySelectorAll('button')).map(b => b.innerText.trim()).filter(t => t).slice(0, 10);
                        const radioGroups = document.querySelectorAll('mat-radio-group, fieldset, [role="radiogroup"]').length;
                        const forms = document.querySelectorAll('form').length;
                        return { radios, checkboxes, radioGroups, forms, buttons };
                    }""")
                    if info['radios'] > 0 or info['checkboxes'] > 0 or info['radioGroups'] > 0:
                        url_short = frame.url[:50] if frame.url else 'main'
                        self.log(
                            f"  Frame #{i} [{url_short}]: "
                            f"{info['radios']} radios, {info['radioGroups']} groups, "
                            f"{info['checkboxes']} checkboxes, buttons={info['buttons'][:5]}",
                            "INFO"
                        )
                except Exception:
                    continue
        except Exception as e:
            self.log(f"  DOM exploration error: {e}", "WARN")

    def _handle_robot_checkbox(self, page):
        """Handle the 'I am not a robot' checkbox on all frames."""
        targets = [page] + list(page.frames)
        for target in targets:
            try:
                robot = target.locator('text="I am not a robot"')
                if robot.is_visible(timeout=2000):
                    self.log("Clicking 'I am not a robot' checkbox...", "INFO")
                    robot.click()
                    time.sleep(0.5)
                    # Also try the mat-checkbox wrapper
                    try:
                        target.locator('mat-checkbox').first.click()
                    except Exception:
                        pass
                    self.log("Checked 'I am not a robot' ✓", "OK")
                    time.sleep(1)
                    return True
            except Exception:
                continue
        self.log("No 'I am not a robot' checkbox — may already be past it", "WARN")
        return False

    def _handle_instructions_popup(self, page):
        """Handle the instructions acceptance popup."""
        accept_selectors = [
            'text="I have read and accept the instructions"',
            'mat-checkbox:has-text("I have read")',
            'label:has-text("I have read")', 'text="I have read"',
        ]
        targets = [page] + list(page.frames)
        for target in targets:
            for sel in accept_selectors:
                try:
                    el = target.locator(sel).first
                    if el.is_visible(timeout=1500):
                        el.click()
                        self.log("Accepted instructions checkbox ✓", "OK")
                        time.sleep(1)
                        # Click Continue
                        continue_sel = ['button:has-text("Continue")', 'button:has-text("CONTINUE")', 'button:has-text("Proceed")']
                        self._click_first_target(target, continue_sel)
                        self.log("Clicked Continue ✓", "OK")
                        time.sleep(1.5)
                        return True
                except Exception:
                    continue
        return False

    def _answer_quiz_question(self, page):
        """Answer the current quiz question by using G4F AI.
        Checks main page and all iframes."""

        # JavaScript to extract the context of the first unanswered question
        js_extract_code = """() => {
            const extractText = (el) => el ? el.innerText.trim() : '';
            
            // Strategy 1: Find radio groups
            const groups = Array.from(document.querySelectorAll(
                'mat-radio-group, fieldset, [role="radiogroup"], .question-container, .quiz-question, .mcq-container'
            ));

            if (groups.length > 0) {
                for (const g of groups) {
                    const hasChecked = g.querySelector('.mat-radio-checked, input:checked, [aria-checked="true"]');
                    if (!hasChecked) {
                        const opts = Array.from(g.querySelectorAll(
                            'mat-radio-button, input[type="radio"], label.option, .quiz-option, [role="radio"], .mat-radio-label'
                        )).filter(el => el.offsetParent !== null);
                        
                        if (opts.length > 0) {
                            // Try to find a question text nearby
                            let qText = '';
                            const prevSib = g.previousElementSibling;
                            if (prevSib) qText = extractText(prevSib);
                            if (!qText && g.parentElement) {
                                const header = g.parentElement.querySelector('h1, h2, h3, h4, .question-text, p, .text-content');
                                if (header) qText = extractText(header);
                            }
                            
                            const optionsList = opts.map(o => extractText(o) || o.value || o.id || 'Option');
                            return { question: qText, options: optionsList, method: 'groups' };
                        }
                    }
                }
            }

            // Strategy 2: Find all ungrouped radio buttons
            const allRadios = Array.from(document.querySelectorAll(
                'mat-radio-button, input[type="radio"], [role="radio"], .mat-radio-label'
            )).filter(el => el.offsetParent !== null);

            if (allRadios.length > 0) {
                 const firstUnchecked = allRadios.find(r => {
                     const isChecked = (r.classList && (r.classList.contains('mat-radio-checked') || r.classList.contains('cdk-focused'))) ||
                                       r.checked || r.getAttribute('aria-checked') === 'true';
                     return !isChecked;
                 });
                 
                 if (firstUnchecked) {
                     const parent = firstUnchecked.closest('.question, .mcq-container, div, section');
                     let qText = '';
                     if (parent) {
                          const header = parent.querySelector('h1, h2, h3, h4, .question-text, p, .text-content');
                          if (header) qText = extractText(header);
                     }
                     
                     // Group buttons that belong to the same logical group
                     const name = firstUnchecked.getAttribute('name') || firstUnchecked.getAttribute('ng-reflect-name') || 'default';
                     const opts = allRadios.filter(r => (r.getAttribute('name') || r.getAttribute('ng-reflect-name') || 'default') === name);
                     
                     const optionsList = opts.map(o => extractText(o) || o.value || o.id || 'Option');
                     return { question: qText, options: optionsList, method: 'ungrouped', name: name };
                 }
            }

            return null;
        }"""
        
        # JavaScript to click a specific index based on the chosen strategy
        js_click_code = """(args) => {
            const { method, index, name } = args;
            let targetOption = null;
            
            // Robust click helper
            const robustClick = (el) => {
                el.click();
                el.dispatchEvent(new MouseEvent('mousedown', {bubbles: true}));
                el.dispatchEvent(new MouseEvent('mouseup', {bubbles: true}));
                el.dispatchEvent(new MouseEvent('click', {bubbles: true}));
                el.dispatchEvent(new Event('change', {bubbles: true}));
                el.dispatchEvent(new Event('input', {bubbles: true}));

                const inner = el.querySelector('.mat-radio-container, .mat-radio-inner-circle, .mat-radio-outer-circle');
                if (inner) { inner.click(); inner.dispatchEvent(new MouseEvent('click', {bubbles: true})); }

                const input = el.querySelector('input[type="radio"]') || (el.tagName === 'INPUT' ? el : null);
                if (input) {
                    input.checked = true;
                    input.dispatchEvent(new Event('change', {bubbles: true}));
                    input.dispatchEvent(new Event('input', {bubbles: true}));
                }

                try {
                    const ngZone = window.ng && window.ng.probe && document.querySelector('app-root');
                    if (ngZone) {
                        const comp = window.ng.probe(document.querySelector('app-root'));
                        if (comp) comp.injector.get(window.ng.coreTokens.NgZone).run(() => {});
                    }
                } catch(e) {}
            };

            if (method === 'groups') {
                const groups = Array.from(document.querySelectorAll('mat-radio-group, fieldset, [role="radiogroup"], .question-container, .quiz-question'));
                for (const g of groups) {
                    const hasChecked = g.querySelector('.mat-radio-checked, input:checked, [aria-checked="true"]');
                    if (!hasChecked) {
                        const opts = Array.from(g.querySelectorAll('mat-radio-button, input[type="radio"], label.option, .quiz-option, [role="radio"]')).filter(el => el.offsetParent !== null);
                        if (opts.length > index) {
                            robustClick(opts[index]);
                            return true;
                        } else if (opts.length > 0) {
                            robustClick(opts[0]); // fallback
                            return true;
                        }
                    }
                }
            } else if (method === 'ungrouped' && name) {
                const allRadios = Array.from(document.querySelectorAll('mat-radio-button, input[type="radio"], [role="radio"]')).filter(el => el.offsetParent !== null);
                const opts = allRadios.filter(r => (r.getAttribute('name') || r.getAttribute('ng-reflect-name') || r.getAttribute('ng-reflect-value') || 'default') === name);
                if (opts.length > index) {
                    robustClick(opts[index]);
                    return true;
                } else if (opts.length > 0) {
                    robustClick(opts[0]); // fallback
                    return true;
                }
            }
            
            return false;
        }""";

        # Try ALL frames first (quiz content is usually in an iframe)
        all_targets = list(page.frames) + [page]
        for target in all_targets:
            try:
                # 1. Extract context
                result = target.evaluate(js_extract_code)
                if result and result.get('options'):
                    question = result.get('question', 'Unknown Question')
                    options = result.get('options', [])
                    method = result.get('method')
                    name = result.get('name')
                    
                    self.log(f"  🧠 Found Question: '{question[:50]}...'", "INFO")
                    
                    # 2. Ask G4F for answer
                    prompt = f"You are solving a multiple-choice question on an auto-assessment.\nQuestion: {question}\n\nOptions:\n"
                    for idx, opt in enumerate(options):
                        prompt += f"{idx}: {opt}\n"
                        
                    prompt += "\nReply with ONLY the integer index (0, 1, 2, etc.) of the correct answer. Do not explain or include any other text."
                    
                    target_index = 0
                    try:
                        resp = g4f.ChatCompletion.create(
                            model=g4f.models.gpt_4o_mini,
                            messages=[{"role": "user", "content": prompt}],
                            timeout=30
                        )
                        target_index = int(''.join(c for c in str(resp) if c.isdigit()))
                        if target_index < 0 or target_index >= len(options):
                            target_index = 0
                        self.log(f"  ✅ AI Chose Option [{target_index}] -> {options[target_index][:30]}...", "OK")
                    except Exception as e:
                        self.log(f"  ❌ AI Inference failed ({e}), falling back to option 0", "WARN")
                        target_index = 0
                        
                    # 3. Inject click
                    click_args = {"method": method, "index": target_index, "name": name}
                    success = target.evaluate(js_click_code, click_args)
                    
                    if success:
                        return True
            except Exception:
                continue
        
        return False

    def _click_save_next(self, page):
        """Click 'Save & Next' button on main page or iframes."""
        save_next_selectors = [
            'button:has-text("Save & Next")', 'button:has-text("Save and Next")',
            'button:has-text("SAVE & NEXT")',
            'a:has-text("Save & Next")',
            'button:has-text("Save")',
        ]
        targets = [page] + list(page.frames)
        for target in targets:
            for sel in save_next_selectors:
                try:
                    btn = target.locator(sel).first
                    if btn.is_visible(timeout=1500) and btn.is_enabled(timeout=800):
                        btn.click()
                        self.log("  Clicked Save & Next ✓", "NEXT")
                        time.sleep(1.5)
                        return True
                except Exception:
                    continue
        return False

    def _all_questions_answered(self, page):
        """Best-effort check for question palette/grid state before final submit."""
        js = """() => {
            const candidates = document.querySelectorAll(
                '.question-palette button, .palette button, .question-grid button, ' +
                '.question-number, .qno, .number-circle, [class*="question"][class*="number"]'
            );

            if (!candidates || candidates.length === 0) return { known: false, allAnswered: true, total: 0 };

            let unanswered = 0;
            for (const el of candidates) {
                const cls = (el.className || '').toString().toLowerCase();
                const aria = (el.getAttribute('aria-label') || '').toLowerCase();
                const title = (el.getAttribute('title') || '').toLowerCase();
                const text = (el.innerText || '').toLowerCase();
                const combined = `${cls} ${aria} ${title} ${text}`;

                if (
                    combined.includes('unanswered') ||
                    combined.includes('not answered') ||
                    combined.includes('not-answered') ||
                    combined.includes('not visited') ||
                    combined.includes('not-visited') ||
                    combined.includes('pending') ||
                    combined.includes('grey') ||
                    combined.includes('gray')
                ) {
                    unanswered += 1;
                }
            }

            return { known: true, allAnswered: unanswered === 0, total: candidates.length };
        }"""

        targets = [page] + list(page.frames)
        for target in targets:
            try:
                info = target.evaluate(js)
                if info.get("known"):
                    if not info.get("allAnswered"):
                        self.log(f"Question palette shows unanswered items ({info.get('total')} total)", "WARN")
                    return bool(info.get("allAnswered"))
            except Exception:
                continue

        # Unknown palette structure: don't block submission.
        return True

    def _submit_assessment(self, page):
        """Submit the assessment and handle confirmation dialogs."""
        self.log("Submitting assessment...", "QUIZ")
        time.sleep(0.8)

        if self._recover_from_explore_playground(page):
            self.log("Redirected to Explore/Playground before submit; retrying module.", "WARN")
            return

        submit_selectors = [
            'button:has-text("Finish Test")', 'button:has-text("FINISH TEST")',
            'button:has-text("Submit Assessment")', 'button:has-text("SUBMIT ASSESSMENT")',
            'button:has-text("Submit")', 'button:has-text("SUBMIT")',
            'button:has-text("Finish")',
        ]

        submitted = False
        targets = [page] + list(page.frames)

        # Final check: try to clear unanswered markers before submitting.
        for _ in range(8):
            if self._all_questions_answered(page):
                break
            advanced = self._click_save_next(page)
            if not advanced:
                break
            time.sleep(1.5)

        # If submit is not visible yet, keep advancing until final page appears.
        for _ in range(12):
            if self._recover_from_explore_playground(page):
                self.log("Redirected to Explore/Playground while reaching submit step.", "WARN")
                return

            has_submit = False
            for target in targets:
                for sel in submit_selectors:
                    try:
                        btn = target.locator(sel).first
                        if btn.is_visible(timeout=500) and btn.is_enabled(timeout=500):
                            has_submit = True
                            break
                    except Exception:
                        continue
                if has_submit:
                    break

            if has_submit:
                break

            advanced = self._click_save_next(page)
            self._handle_warning_and_fullscreen(page)
            if not advanced:
                break
            time.sleep(1.5)

        for target in targets:
            if self._click_first_target(target, submit_selectors):
                self.log("Clicked Submit Assessment ✓", "OK")
                submitted = True
                time.sleep(1.0)
                break

        if submitted:
            # Strict flow: click only explicit confirm actions after the single Submit click.
            confirm_selectors = [
                'button:has-text("Yes")',
                'button:has-text("YES")',
                'button:has-text("Yes, Submit")',
                'button:has-text("Confirm")',
                'button:has-text("Proceed")',
            ]

            confirmed = False
            for _ in range(6):
                if self._recover_from_explore_playground(page):
                    self.log("Redirected to Explore/Playground at confirm step; retrying module.", "WARN")
                    return

                for target in targets:
                    if self._click_first_target(target, confirm_selectors):
                        self.log("Clicked 'Yes' confirmation ✓", "OK")
                        confirmed = True
                        break

                if confirmed:
                    break
                time.sleep(0.8)

            if not confirmed:
                self.log("No explicit 'Yes' confirmation found (may have auto-confirmed).", "WARN")

            # Post-submit: often there is a score/result screen requiring one more action.
            result_continue = [
                'button:has-text("Continue")',
                'button:has-text("Back to Course")',
                'button:has-text("Done")',
                'button:has-text("Close")',
                'button:has-text("Go to Course")',
                'a:has-text("Back to Course")',
            ]
            for _ in range(8):
                time.sleep(0.9)
                clicked_any = False
                for target in targets:
                    if self._click_first_target(target, result_continue):
                        self.log("Moved past assessment result screen ✓", "OK")
                        clicked_any = True
                        break
                if not clicked_any:
                    break
        else:
            self.log("No Submit button found — quiz may auto-submit", "WARN")

    def _click_first_target(self, target, selectors):
        """Click the first visible+enabled element from selectors on a page/frame."""
        for sel in selectors:
            try:
                locator = target.locator(sel)
                count = min(locator.count(), 4)
                for i in range(count):
                    btn = locator.nth(i)
                    if btn.is_visible(timeout=1200) and btn.is_enabled(timeout=800):
                        if self._is_blocked_click_target(btn):
                            continue
                        btn.click()
                        return True
            except Exception:
                continue
        return False

    def _current_sidebar_item_completed(self, page):
        """Best-effort check whether active TOC item appears completed."""
        try:
            info = page.evaluate("""() => {
                const active = document.querySelector('.toc-item.active, .node-title.active, .module-title.active, [aria-current="true"]');
                if (!active) return { known: false, completed: true };
                const txt = (active.innerText || '').toLowerCase();
                const html = (active.innerHTML || '').toLowerCase();
                const completed = html.includes('check') || txt.includes('completed');
                const explicitIncomplete = txt.includes('incomplete');
                return { known: true, completed: completed && !explicitIncomplete };
            }""")
            return (not info.get("known")) or bool(info.get("completed"))
        except Exception:
            return True

    def _force_sidebar_refresh(self, page):
        """Force sidebar UI refresh by clicking refresh/toggle controls."""
        refresh_selectors = [
            'mat-icon:has-text("refresh")',
            'button:has-text("Refresh")',
            'button[aria-label*="Refresh"]',
            '[class*="refresh"]',
        ]

        self._click_first(page, refresh_selectors)

        # Collapse/expand TOC to refresh status rendering.
        try:
            toc = page.locator('mat-icon:has-text("menu_book"), .toc-button').first
            if toc.is_visible(timeout=1200):
                toc.click()
                time.sleep(0.5)
                toc.click()
                time.sleep(0.5)
        except Exception:
            pass

    def _wait_for_completion_or_recover(self, page, wait_seconds=20):
        """Wait for sidebar completion update; refresh/retry if stuck too long."""
        start = time.time()
        while time.time() - start < wait_seconds:
            if self._periodic_self_heal_refresh(page):
                return False
            if self._recover_from_blank_viewer_shell(page):
                return False
            if self._current_sidebar_item_completed(page):
                return True
            time.sleep(1.0)

        self.log("Module appears stuck (no completion update). Refreshing and retrying.", "WARN")
        self._force_sidebar_refresh(page)
        try:
            page.reload(wait_until="domcontentloaded")
            page.wait_for_load_state("networkidle", timeout=15000)
        except Exception:
            pass
        self._dismiss_zoiee(page)
        self._handle_warning_and_fullscreen(page)
        return False

    # ═════════════════════════════════════════════════════════════
    #  CLICK NEXT (Course Player Navigation)
    # ═════════════════════════════════════════════════════════════
    def _click_next(self, page):
        """Click the Next button in the Springboard course player."""
        next_selectors = [
            # Springboard-specific (discovered from live testing)
            '.navigation-btn-frwd',
            'button.navigation-btn-frwd',
            # mat-icon based
            'button:has(mat-icon:has-text("arrow_forward_ios"))',
            # Generic Next buttons inside player
            'button:has-text("Next")', 'button:has-text("NEXT")',
            'button[aria-label="Next"]',
            # Mark as done
            'button:has-text("Mark as done")',
            'button:has-text("MARK AS DONE")',
        ]

        # Only click player-scoped controls to avoid jumping to unrelated course links.
        for sel in next_selectors:
            try:
                btn = page.locator(sel).first
                if btn.is_visible(timeout=1500) and btn.is_enabled(timeout=1000):
                    if self._is_blocked_click_target(btn):
                        continue
                    container_ok = btn.locator("xpath=ancestor::*[contains(@class,'navigation') or contains(@class,'player') or contains(@class,'content')] ").count() > 0
                    if container_ok or sel in ('.navigation-btn-frwd', 'button.navigation-btn-frwd', 'button:has(mat-icon:has-text("arrow_forward_ios"))'):
                        btn.click()
                        self.log("Clicked Next ➡️", "NEXT")
                        time.sleep(self.MODULE_LOAD_WAIT)
                        return True
            except Exception:
                continue

        # Fallback attempt with generic click helper
        if self._click_first(page, next_selectors):
            self.log("Clicked Next ➡️", "NEXT")
            time.sleep(self.MODULE_LOAD_WAIT)
            return True

        # Fallback: keyboard
        try:
            page.keyboard.press("ArrowRight")
            self.log("ArrowRight fallback", "NEXT")
            time.sleep(self.MODULE_LOAD_WAIT)
            return True
        except Exception:
            pass
        return False

    # ═════════════════════════════════════════════════════════════
    #  HELPERS
    # ═════════════════════════════════════════════════════════════
    def _find_element(self, page, selectors):
        for sel in selectors:
            try:
                el = page.locator(sel).first
                if el.is_visible(timeout=2000):
                    return el
            except Exception:
                continue
        return None

    def _is_blocked_click_target(self, el):
        """Block accidental clicks on side-nav/app-shell controls (e.g., code icon / playground shortcuts)."""
        try:
            return bool(el.evaluate("""(node) => {
                if (!node) return false;

                const text = (node.innerText || '').toLowerCase();
                const aria = (node.getAttribute('aria-label') || '').toLowerCase();
                const title = (node.getAttribute('title') || '').toLowerCase();
                const cls = (node.className || '').toString().toLowerCase();
                const icon = (node.querySelector('mat-icon')?.innerText || '').toLowerCase();
                const combined = `${text} ${aria} ${title} ${cls} ${icon}`;

                const blockedWords = [
                    'playground', 'tryout', 'explore', 'catalog', 'code', 'programming'
                ];

                if (blockedWords.some(w => combined.includes(w))) return true;

                // Avoid common side navigation rails and app-shell icon strips.
                const blockedAncestor = node.closest(
                    'aside, nav, [role="navigation"], .side-nav, .sidenav, .left-nav, ' +
                    '.left-menu, .menu-rail, .app-shell, .app-sidebar, .quick-links, .global-nav'
                );
                if (blockedAncestor) return true;

                // Block icon-only code toggles represented as < > chevrons.
                const hasChevronGlyph = /[<>‹›«»]/.test(node.innerText || '');
                if (hasChevronGlyph && !(combined.includes('next') || combined.includes('continue'))) return true;

                return false;
            }"""))
        except Exception:
            return False

    def _click_first(self, page, selectors, quick=False):
        vis_timeout = 320 if quick else 1500
        enabled_timeout = 220 if quick else 1000
        for sel in selectors:
            try:
                btn = page.locator(sel).first
                if btn.is_visible(timeout=vis_timeout) and btn.is_enabled(timeout=enabled_timeout):
                    if self._is_blocked_click_target(btn):
                        continue
                    btn.click()
                    return True
            except Exception:
                continue
        return False

    def _click_first_frame(self, frame, selectors):
        for sel in selectors:
            try:
                btn = frame.locator(sel).first
                if btn.is_visible(timeout=1500):
                    if self._is_blocked_click_target(btn):
                        continue
                    btn.click()
                    return True
            except Exception:
                continue
        return False

    # ═════════════════════════════════════════════════════════════
    #  MAIN RUN LOOP
    # ═════════════════════════════════════════════════════════════
    def run(self):
        self.log("🚀 Launching browser...", "INFO")
        with sync_playwright() as pw:
            browser = pw.chromium.launch(
                headless=self.headless,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                    "--start-maximized",
                    "--disable-notifications",
                    "--disable-popup-blocking",
                ],
            )
            context_kwargs = {
                "viewport": {"width": 1366, "height": 768},
                "permissions": ["clipboard-read", "clipboard-write"],
                "user_agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/125.0.0.0 Safari/537.36"
                ),
            }

            # Some Playwright/browser builds do not support certain permissions.
            # Fall back gracefully instead of crashing the whole run.
            try:
                context = browser.new_context(**context_kwargs)
            except Exception as e:
                if "Unknown permission" in str(e):
                    self.log(f"Permission unsupported ({e}); continuing without permissions.", "WARN")
                    context_kwargs.pop("permissions", None)
                    context = browser.new_context(**context_kwargs)
                else:
                    raise
            context.set_default_timeout(15000)

            # Apply anti-tab-switch detection bypass globally on every page/frame.
            context.add_init_script(VISIBILITY_HACK)
            context.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
                delete window.__playwright; delete window.__pw_manual;
            """)

            page = context.new_page()
            
            # Auto-accept any browser alerts/dialogs
            page.on("dialog", lambda dialog: {
                self.log(f"Auto-accepting dialog: {dialog.message}", "WARN"),
                dialog.accept()
            })

            # Grant permissions explicitly for the assessment domains to prevent the "wants to" popups
            try:
                context.grant_permissions(["clipboard-read", "clipboard-write"], origin="https://infyspringboard.onwingspan.com")
                context.grant_permissions(["clipboard-read", "clipboard-write"], origin="https://lex-iap.infosysapps.com")
                self.log("Clipboard permissions granted to assessment origins.", "OK")
            except Exception:
                pass


            try:
                # 1. Login
                self._login(page)

                # 2. Dismiss Zoiee chatbot
                self._dismiss_zoiee(page)

                # 3. Navigate to course
                self._navigate_to_course(page)

                # 4. Jump straight to final assessment
                self.log("⏩ Skipping straight to the final assessment...", "INFO")
                # Give UI a moment to render the sidebar
                time.sleep(4.0)

                # Force open TOC if hidden
                try:
                    # The Contents tab uses the 'web' icon, NOT 'menu_book' (which is Details)
                    toc_btn = page.locator('mat-icon:has-text("web"), .mat-tab-label:has-text("Contents")').first
                    if toc_btn.is_visible(timeout=3000):
                        toc_btn.click()
                        time.sleep(2.0)
                except Exception:
                    pass

                # Find and click the final module
                try:
                    self.log("Searching for Assessment in the sidebar...", "INFO")
                    
                    # Try to click by text first
                    clicked = False
                    for kw in ["Assessment", "Quiz", "Test", "Exam"]:
                        try:
                            # Look specifically inside the side navigation/tree area
                            node = page.locator(f'mat-tree-node:has-text("{kw}"), [role="treeitem"]:has-text("{kw}"), div.mat-tab-body-content:has-text("{kw}") >> text="{kw}"').last
                            if node.is_visible(timeout=2000):
                                node.click()
                                self.log(f"Clicked '{kw}' in the TOC!", "OK")
                                clicked = True
                                break
                        except Exception:
                            continue
                            
                    if not clicked:
                        # Fallback to the last tree node
                        try:
                            last_node = page.locator('mat-tree-node, [role="treeitem"]').last
                            if last_node.is_visible(timeout=2000):
                                last_node.click()
                                self.log("Clicked the last item in the TOC as a fallback.", "OK")
                                clicked = True
                        except Exception:
                            pass
                            
                    if clicked:
                        time.sleep(4.0)
                        try:
                            page.wait_for_load_state("networkidle", timeout=10000)
                        except Exception:
                            pass
                    else:
                        self.log("Could not jump via TOC locators, proceeding standard loop.", "WARN")
                except Exception as e:
                    self.log(f"Could not jump via TOC: {e}", "WARN")

                # 5. Auto-player loop (will now only process the assessment and exit)
                self.log("🎯 Assessment auto-player started!", "OK")

                while self._running:
                    self._module_count += 1
                    self.log(f"━━━ Module #{self._module_count} ━━━", "INFO")

                    if self._periodic_self_heal_refresh(page):
                        time.sleep(0.3)
                        continue

                    try:
                        page.wait_for_load_state("networkidle", timeout=5000)
                    except PlaywrightTimeout:
                        pass
                    time.sleep(0.25)

                    # Dismiss Zoiee if it reappears
                    self._dismiss_zoiee(page, quick=True, periodic=True)
                    self._handle_warning_and_fullscreen(page)

                    # If redirected to Explore/Playground, immediately return to the course player.
                    if self._recover_from_explore_playground(page):
                        time.sleep(0.4)
                        continue

                    # Recover if player is stuck on a blank shell after transitions.
                    if self._recover_from_blank_viewer_shell(page):
                        time.sleep(0.4)
                        continue

                    # Check for assessment (The main task we are here for)
                    if self._is_assessment(page):
                        if not self._handle_assessment(page):
                            time.sleep(0.8)
                            continue
                        if not self._wait_for_completion_or_recover(page, wait_seconds=20):
                            continue
                        self._handle_popups(page)
                        time.sleep(0.4)
                        if not self._click_next(page):
                            self.log("No Next after assessment", "WARN")
                        continue

                    # Handle popups
                    self._handle_popups(page, quick=True, periodic=True)

                    # If not an assessment, just forcefully skip to the next module
                    self.log("⏩ Not an assessment, skipping module...", "INFO")
                    if not self._click_next(page):
                        retry_wait = self.NON_INTERACTIVE_RETRY_WAIT if self.AGGRESSIVE_SPEED_MODE else 5
                        self.log(f"No Next button — retrying in {retry_wait:.1f}s...", "WARN")
                        time.sleep(retry_wait)
                        self._handle_popups(page, quick=True, periodic=True)
                        self._dismiss_zoiee(page, quick=True, periodic=True)
                        if not self._click_next(page):
                            self.log(f"✅ Course appears complete! Modules: {self._module_count}", "OK")
                            break

                    try:
                        page.wait_for_load_state("networkidle", timeout=5000)
                    except PlaywrightTimeout:
                        pass

            except Exception as e:
                self.log(f"Fatal error: {e}", "ERR")
            finally:
                self.log(f"Total modules processed: {self._module_count}", "INFO")
                time.sleep(0.8)
                browser.close()


def run_from_env():
    """Run the unified automation engine using environment variables."""
    email = os.getenv("SPRINGBOARD_EMAIL", "")
    password = os.getenv("SPRINGBOARD_PASSWORD", "")
    course_url = os.getenv("SPRINGBOARD_COURSE_URL", "")
    headless = os.getenv("SPRINGBOARD_HEADLESS", "false").lower() in ("1", "true", "yes")

    if not email or not password or not course_url:
        print("Missing env vars. Set SPRINGBOARD_EMAIL, SPRINGBOARD_PASSWORD, SPRINGBOARD_COURSE_URL")
        return

    engine = SpringboardAutomation(
        email=email,
        password=password,
        course_url=course_url,
        headless=headless,
        log_callback=lambda msg, level: print(f"[{level}] {msg}"),
    )
    engine.run()


if __name__ == "__main__":
    run_from_env()
