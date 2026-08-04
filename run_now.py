from springboard_engine import SpringboardAutomation

def run_now():
    print("[>] Starting automation for requested course...")
    engine = SpringboardAutomation(
        email="YOUR_EMAIL_HERE",
        password="YOUR_PASSWORD_HERE",
        course_url="https://infyspringboard.onwingspan.com/web/en/app/toc/lex_auth_0127667384693882883448_shared/overview",
        headless=False,
        log_callback=lambda msg, level: print(f"[{level}] {msg}".encode("cp1252", errors="replace").decode("cp1252"))
    )
    engine.run()
    print("[OK] Finished!")

if __name__ == "__main__":
    run_now()
