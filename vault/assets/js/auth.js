/* Enhance the existing POST forms; cookies and destinations stay server-owned. */
(function () {
    "use strict";
    const form = document.querySelector(".auth-card form");
    if (!form) return;
    const username = form.elements.username;
    const password = form.elements.password;
    const submit = form.querySelector('[type="submit"]');
    const toggle = document.querySelector("#password-toggle");
    const error = document.querySelector("#auth-error");
    const status = document.querySelector("#auth-status");
    const messages = JSON.parse(form.dataset.messages);
    const label = submit.textContent;
    let pending = false;
    let controller;

    function passwordVisibility(show) {
        const start = password.selectionStart;
        const end = password.selectionEnd;
        const direction = password.selectionDirection;
        const text = show ? toggle.dataset.hide : toggle.dataset.show;
        const type = show ? "text" : "password";
        password.type = type;
        toggle.setAttribute("aria-pressed", String(show));
        toggle.setAttribute("aria-label", text);
        toggle.title = text;
        toggle.querySelector("[data-password-label]").textContent = text;
        toggle.querySelectorAll("[data-password-icon]").forEach(icon => {
            icon.toggleAttribute("hidden", icon.dataset.passwordIcon !== (show ? "hide" : "show"));
        });
        if (typeof start === "number" && typeof end === "number") {
            password.setSelectionRange(start, end, direction);
            // Chromium can reset a focused input's selection while rebuilding
            // its renderer after a type change. Restore it after that update.
            requestAnimationFrame(() => {
                if (password.type === type && document.activeElement === password) {
                    password.setSelectionRange(start, end, direction);
                }
            });
        }
    }

    function busy(value) {
        pending = value;
        form.setAttribute("aria-busy", String(value));
        submit.disabled = value;
        toggle.disabled = value;
        username.readOnly = password.readOnly = value;
        submit.textContent = value ? submit.dataset.pending : label;
        status.textContent = value ? submit.dataset.pending : "";
    }

    function fieldError(input, code) {
        const hint = document.querySelector(`#${input.id}-error`);
        input.setAttribute("aria-invalid", String(Boolean(code)));
        hint.textContent = code ? messages[code] : "";
        hint.hidden = !code;
    }

    function report(code) {
        // Only fixed local copy reaches the UI, never server details or inputs.
        error.textContent = messages[code] || messages.unavailable;
        error.hidden = false;
        if (code === "invalid_credentials") {
            password.value = "";
            passwordVisibility(false);
            fieldError(password, code);
            password.focus();
        } else if (code === "invalid_username" || code === "weak_password") {
            const input = code === "invalid_username" ? username : password;
            fieldError(input, code);
            input.focus();
        } else error.focus();
    }

    form.noValidate = true;
    toggle.hidden = false;
    toggle.parentElement.classList.add("auth-password--toggle");
    toggle.addEventListener("pointerdown", event => {
        // Touch/mouse users keep their caret and mobile keyboard while editing.
        // Keyboard and D-pad activation retain the button's normal focus.
        if (document.activeElement === password) event.preventDefault();
    });
    toggle.addEventListener("click", () => {
        passwordVisibility(password.type === "password");
    });

    [username, password].forEach(input => {
        input.addEventListener("input", () => fieldError(input));
    });
    username.addEventListener("keydown", event => {
        if (event.key === "Enter" && !event.isComposing) {
            event.preventDefault();
            password.focus();
        }
    });

    form.addEventListener("submit", async event => {
        event.preventDefault();
        if (pending) return;
        error.hidden = true;
        fieldError(username);
        fieldError(password);
        let invalid;
        if (!username.value.trim()) { fieldError(username, "username_required"); invalid = username; }
        if (!password.value) { fieldError(password, "password_required"); invalid = invalid || password; }
        if (form.dataset.setup === "true") {
            if (username.value && (!/^[A-Za-z0-9][A-Za-z0-9_.-]{2,31}$/.test(username.value) || username.value.includes(".."))) {
                fieldError(username, "invalid_username"); invalid = invalid || username;
            }
            if (password.value && !(password.value.length >= 8 && /[a-z]/.test(password.value) &&
                /[A-Z]/.test(password.value) && /[0-9]/.test(password.value) && /[!@#$%^&*(),.?":{}|<>]/.test(password.value))) {
                fieldError(password, "weak_password"); invalid = invalid || password;
            }
        }
        if (invalid) { invalid.focus(); return; }

        passwordVisibility(false);
        const body = new FormData(form);
        busy(true);
        controller = new AbortController();
        const timeout = setTimeout(() => controller.abort(), 15000);
        let navigating = false;
        try {
            const response = await fetch(form.action, {method: "POST", body,
                credentials: "same-origin", headers: {Accept: "application/json"}, signal: controller.signal});
            const result = await response.json();
            if (response.ok && typeof result.redirect === "string") {
                const target = new URL(result.redirect, location.origin);
                if (target.origin !== location.origin) throw new Error("Invalid destination");
                navigating = true;
                location.assign(target.href);
            } else {
                busy(false);
                const codes = ["invalid_credentials", "blocked", "invalid_username", "weak_password", "action_not_allowed"];
                report(codes.includes(result.code) ? result.code : "unavailable");
            }
        } catch (failure) {
            busy(false);
            report(failure instanceof TypeError || failure.name === "AbortError" ? "network" : "unavailable");
        } finally {
            clearTimeout(timeout);
            if (!navigating) busy(false);
        }
    });

    // Up/down move between form controls on a remote; left/right keep editing.
    document.addEventListener("keydown", event => {
        if (event.defaultPrevented || event.altKey || event.ctrlKey || event.metaKey || event.shiftKey ||
            !["ArrowUp", "ArrowDown"].includes(event.key) || !form.contains(event.target)) return;
        const controls = Array.from(form.querySelectorAll('input:not([type="hidden"]), button'))
            .filter(control => !control.disabled && !control.hidden);
        const position = controls.indexOf(document.activeElement);
        const next = controls[position + (event.key === "ArrowDown" ? 1 : -1)] ||
            (position === 0 && event.key === "ArrowUp" ? document.querySelector(".auth-language .active") : null);
        if (next) {
            event.preventDefault();
            next.focus();
            next.scrollIntoView({block: "nearest"});
        }
    });

    window.addEventListener("pagehide", () => {
        if (controller) controller.abort();
        password.value = "";
        passwordVisibility(false);
    });
    window.addEventListener("pageshow", event => {
        if (event.persisted) {
            busy(false);
            password.value = "";
            passwordVisibility(false);
        }
    });
})();
