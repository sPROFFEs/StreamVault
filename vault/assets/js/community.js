(() => {
    "use strict";
    document.addEventListener("DOMContentLoaded", async () => {
        const byId = id => document.getElementById(id);
        const form = byId("profile-form");
        if (!form) return;
        const es = window.APP_LANG === "es";
        const t = (en, spanish) => es ? spanish : en;
        let page = 1, attempt = 0;
        const element = (tag, text) => { const node = document.createElement(tag); if (text != null) node.textContent = text; return node; };
        function feedback(text, error = false) {
            byId("community-feedback").textContent = text;
            byId("community-feedback").classList.toggle("operations-error", error);
        }
        async function request(path, method = "GET", body) {
            const response = await fetch(path, {method, credentials: "same-origin", signal: AbortSignal.timeout(20000),
                headers: body ? {"Content-Type": "application/json"} : {}, body: body ? JSON.stringify(body) : undefined});
            const data = await response.json();
            if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : `HTTP ${response.status}`);
            return data;
        }
        function metrics(data) {
            const list = element("dl");
            const labels = {movies_completed: t("Movies completed", "Películas terminadas"), episodes_completed: t("Episodes completed", "Episodios terminados"),
                entries_started: t("Entries started", "Títulos/episodios iniciados"), favorites: t("Favorites", "Favoritos")};
            Object.entries(labels).forEach(([key, label]) => { list.append(element("dt", label), element("dd", String(data[key] || 0))); });
            return list;
        }
        function profile(item) {
            const card = element("article");
            card.className = "profile-card";
            card.append(element("h3", item.display_name), element("p", item.bio));
            if (item.node_name) card.append(element("p", item.node_name));
            if (item.metrics) card.append(metrics(item.metrics));
            return card;
        }
        async function loadPeople(append = false) {
            const current = ++attempt;
            const peer = byId("community-node").value;
            try {
                const data = await request(peer === "local" ? `/api/profiles?page=${page}&limit=50` : `/api/federation/peers/${peer}/profiles`);
                if (current !== attempt) return;
                const cards = (data.items || []).map(profile);
                if (append) byId("community-profiles").append(...cards);
                else byId("community-profiles").replaceChildren(...cards);
                byId("community-more").hidden = peer !== "local" || cards.length < 50;
                if (!cards.length && !append) feedback(t("No shared profiles yet.", "Todavía no hay perfiles compartidos."));
            } catch (error) { if (current === attempt) { byId("community-profiles").replaceChildren(); feedback(error.message, true); } }
        }
        function populate(data) {
            ["display_name", "bio", "visibility"].forEach(key => { form.elements[key].value = data[key]; });
            form.elements.show_metrics.checked = data.show_metrics;
            const card = element("div"); card.className = "profile-card"; card.append(metrics(data.metrics || {}));
            byId("my-profile-metrics").replaceChildren(card);
        }
        form.addEventListener("submit", async event => {
            event.preventDefault();
            const button = form.querySelector("button");
            if (button.disabled) return;
            button.disabled = true;
            try {
                populate(await request("/api/profiles/me", "PUT", {display_name: form.elements.display_name.value, bio: form.elements.bio.value,
                    visibility: form.elements.visibility.value, show_metrics: form.elements.show_metrics.checked}));
                feedback(t("Profile saved.", "Perfil guardado."));
                page = 1;
                await loadPeople();
            } catch (error) { feedback(error.message, true); }
            finally { button.disabled = false; }
        });
        byId("community-node").addEventListener("change", () => { page = 1; feedback(""); void loadPeople(); });
        byId("community-more").addEventListener("click", async () => {
            byId("community-more").disabled = true;
            page++;
            try { await loadPeople(true); } finally { byId("community-more").disabled = false; }
        });
        try {
            populate(await request("/api/profiles/me"));
            const data = await request("/api/federation/peers");
            (data.items || []).forEach(peer => {
                const option = element("option", `${peer.name} · ${peer.status}`);
                option.value = String(peer.id); byId("community-node").append(option);
            });
            await loadPeople();
        } catch (error) { feedback(error.message, true); }
    });
})();
