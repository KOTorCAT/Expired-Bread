/* Expired Bread — минимальный прогрессивный JS.
   Никаких фреймворков и сборки: только то, что улучшает работу с формами.
   Всё в конструкции IIFE, чтобы не засорять глобальную область. */

(function () {
    "use strict";

    /* --- Зона загрузки файла ------------------------------------------- */

    document.querySelectorAll("[data-dropzone]").forEach(function (zone) {
        var input = zone.querySelector('input[type="file"]');
        var label = zone.querySelector("[data-dropzone-file]");
        if (!input) return;

        function showName() {
            if (!label) return;
            var file = input.files && input.files[0];
            label.textContent = file ? file.name : "";
        }

        input.addEventListener("change", showName);

        ["dragenter", "dragover"].forEach(function (event) {
            zone.addEventListener(event, function (e) {
                e.preventDefault();
                zone.classList.add("is-dragover");
            });
        });

        ["dragleave", "drop"].forEach(function (event) {
            zone.addEventListener(event, function (e) {
                e.preventDefault();
                zone.classList.remove("is-dragover");
            });
        });

        zone.addEventListener("drop", function (e) {
            if (!e.dataTransfer || !e.dataTransfer.files.length) return;
            input.files = e.dataTransfer.files;
            showName();
        });
    });

    /* --- Долгая обработка: честная надпись на кнопке -------------------- */

    document.querySelectorAll("form[data-processing-form]").forEach(function (form) {
        form.addEventListener("submit", function () {
            var button = form.querySelector('button[type="submit"]');
            if (!button || button.disabled) return;

            var original = button.textContent;
            button.textContent = "Обрабатываю, это может занять до минуты…";
            button.disabled = true;
            button.setAttribute("aria-busy", "true");

            // Если сервер вернул страницу с ошибкой — кнопку надо вернуть,
            // иначе форма останется заблокированной.
            window.setTimeout(function () {
                button.disabled = false;
                button.textContent = original;
                button.removeAttribute("aria-busy");
            }, 120000);
        });
    });

    /* --- Автовысота текстовых полей ------------------------------------ */

    document.querySelectorAll("textarea[data-autogrow]").forEach(function (area) {
        function grow() {
            area.style.height = "auto";
            area.style.height = Math.min(area.scrollHeight, 420) + "px";
        }
        area.addEventListener("input", grow);
        grow();
    });

    /* --- Таблицы редактора: добавить и удалить строку ------------------- */

    document.querySelectorAll("[data-editor-table]").forEach(function (group) {
        var body = group.querySelector("[data-rows]");
        var template = group.querySelector("[data-row-template]");
        var addButton = group.querySelector("[data-add-row]");
        var counter = Number(group.getAttribute("data-next-index") || 0);

        if (addButton && body && template) {
            addButton.addEventListener("click", function () {
                var html = template.innerHTML.replace(/__NEW__/g, String(counter));
                counter += 1;
                body.insertAdjacentHTML("beforeend", html);
            });
        }

        group.addEventListener("click", function (event) {
            var button = event.target.closest("[data-remove-row]");
            if (!button) return;
            var row = button.closest("[data-row]");
            if (row) row.remove();
        });
    });

    /* --- Подтверждение опасных действий -------------------------------- */

    var dialog = document.getElementById("confirm-dialog");

    document.querySelectorAll("form[data-confirm]").forEach(function (form) {
        form.addEventListener("submit", function (event) {
            // Без поддержки <dialog> остаётся системное окно браузера.
            if (!dialog || typeof dialog.showModal !== "function") {
                if (!window.confirm(form.getAttribute("data-confirm"))) {
                    event.preventDefault();
                }
                return;
            }

            if (form.dataset.confirmed === "1") return;

            event.preventDefault();

            var text = dialog.querySelector("[data-confirm-text]");
            if (text) text.textContent = form.getAttribute("data-confirm") || "";

            dialog.returnValue = "";
            dialog.showModal();

            dialog.addEventListener("close", function handler() {
                dialog.removeEventListener("close", handler);
                if (dialog.returnValue === "confirm") {
                    form.dataset.confirmed = "1";
                    form.submit();
                }
            });
        });
    });

    /* --- Живой статус обработки ---------------------------------------- */

    document.querySelectorAll("[data-status-poll]").forEach(function (root) {
        var current = root.getAttribute("data-status-poll");
        var url = root.getAttribute("data-status-url");
        if (!url) return;
        if (current !== "processing" && current !== "uploaded") return;

        var elapsed = root.querySelector("[data-elapsed]");
        var started = Number(root.getAttribute("data-started-at") || 0) * 1000;

        // Если сервер не отдал время старта — считаем от открытия страницы,
        // чтобы счётчик в любом случае шёл, а не висел на нуле.
        if (!started || isNaN(started)) started = Date.now();

        function showElapsed() {
            if (!elapsed) return;
            var seconds = Math.max(0, Math.round((Date.now() - started) / 1000));
            elapsed.textContent = seconds + " с";
        }

        showElapsed();
        var ticker = window.setInterval(showElapsed, 1000);

        var poller = window.setInterval(function () {
            fetch(url, { headers: { "X-Requested-With": "XMLHttpRequest" } })
                .then(function (response) { return response.ok ? response.json() : null; })
                .then(function (data) {
                    if (!data || data.status === current) return;
                    window.clearInterval(ticker);
                    window.clearInterval(poller);
                    window.location.reload();
                })
                .catch(function () { /* сеть отвалилась — ждём следующий тик */ });
        }, 2000);
    });

    /* --- Фильтры: применяем сразу при выборе --------------------------- */

    document.querySelectorAll("[data-auto-submit]").forEach(function (control) {
        control.addEventListener("change", function () {
            if (control.form) control.form.submit();
        });
    });

    /* --- Согласие на обезличенные документы ---------------------------- */

    var consentKey = "eb_consent_v1";
    var consentPending = true;
    try { if (window.localStorage.getItem(consentKey) === "1") consentPending = false; } catch (e) {}

    if (consentPending && document.body.classList.contains("is-app")) {
        var consentDialog = document.getElementById("consent-dialog");
        var consentCheck = document.querySelector("[data-consent-check]");
        var consentBtn = document.querySelector("[data-consent-ok]");
        if (consentDialog && typeof consentDialog.showModal === "function" && consentCheck && consentBtn) {
            function consentSync() { consentBtn.disabled = !consentCheck.checked; }
            consentCheck.addEventListener("change", consentSync);
            consentSync();
            consentDialog.showModal();
            consentDialog.addEventListener("close", function () {
                if (consentCheck.checked) {
                    try { window.localStorage.setItem(consentKey, "1"); } catch (e) {}
                }
            });
        }
    }

    /* --- aria-current на активном пункте меню -------------------------- */

    document.querySelectorAll(".rail__item.is-active").forEach(function (el) {
        el.setAttribute("aria-current", "page");
    });


    /* --- Плавная смена разделов ---------------------------------------- */

    (function () {
        if (!window.matchMedia) return;
        if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;

        var targets = document.querySelectorAll("a.rail__item, a.brand");
        if (!targets.length) return;

        var leaving = false;

        function go(href) {
            if (leaving) return;
            leaving = true;
            document.body.classList.add("is-leaving");
            window.setTimeout(function () { window.location.assign(href); }, 180);
        }

        targets.forEach(function (link) {
            link.addEventListener("click", function (event) {
                if (event.defaultPrevented) return;
                if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
                if (event.button !== 0) return;
                if (link.target && link.target !== "_self") return;
                if (link.hasAttribute("download")) return;

                var href = link.getAttribute("href");
                if (!href || href.charAt(0) === "#") return;
                if (link.pathname === window.location.pathname && link.search === window.location.search) return;

                event.preventDefault();
                go(link.href);
            });
        });
    })();


    /* --- «Спросить»: ответ приходит без перезагрузки ------------------- */

    (function () {
        var form = document.querySelector("form[data-ask-form]");
        var feed = document.getElementById("qa-history");
        if (!form || !feed || !window.fetch) return;

        function buildPending(question) {
            var card = document.createElement("div");
            card.className = "card mt-16";
            card.setAttribute("data-qa-pending", "");

            var head = document.createElement("div");
            head.className = "card__head";
            var title = document.createElement("h2");
            title.textContent = question;
            var badge = document.createElement("span");
            badge.className = "badge badge--processing";
            badge.textContent = "ищу ответ";
            head.appendChild(title);
            head.appendChild(badge);

            var body = document.createElement("div");
            body.className = "card__body";
            var answer = document.createElement("div");
            answer.className = "answer answer--none";
            var chip = document.createElement("span");
            chip.className = "chip chip--progress";
            chip.textContent = "модель думает…";
            answer.appendChild(chip);
            body.appendChild(answer);

            card.appendChild(head);
            card.appendChild(body);
            return card;
        }

        form.addEventListener("submit", function (event) {
            var input = form.querySelector("#question");
            if (!input || !input.value.trim()) return;
            event.preventDefault();

            var empty = feed.querySelector(".empty");
            if (empty) {
                var emptyCard = empty.closest(".card");
                if (emptyCard) emptyCard.remove();
            }

            var pending = buildPending(input.value.trim());
            feed.insertBefore(pending, feed.firstChild);

            var url = form.getAttribute("action") || window.location.href;
            var data = new FormData(form);
            input.value = "";

            fetch(url, {
                method: "POST",
                body: data,
                headers: { "X-Requested-With": "XMLHttpRequest" },
                credentials: "same-origin"
            })
                .then(function (response) { return response.ok ? response.text() : null; })
                .then(function (html) {
                    if (!html) { window.location.reload(); return; }
                    var wrap = document.createElement("div");
                    wrap.innerHTML = html.trim();
                    var item = wrap.firstElementChild;
                    if (item) feed.insertBefore(item, pending);
                    pending.remove();
                })
                .catch(function () { window.location.reload(); });
        });
    })();


    /* --- «Открыть»: показываем PDF прямо на странице ------------------- */

    (function () {
        var trigger = document.querySelector("[data-open-preview]");
        var preview = document.getElementById("doc-preview");
        if (!trigger || !preview) return;
        trigger.addEventListener("click", function () {
            var frame = preview.querySelector("iframe");
            if (frame && !frame.getAttribute("src")) {
                frame.setAttribute("src", frame.getAttribute("data-src") || "");
            }
            preview.hidden = false;
            preview.scrollIntoView({ behavior: "smooth", block: "start" });
        });

        var closeBtn = preview.querySelector("[data-close-preview]");
        if (closeBtn) {
            closeBtn.addEventListener("click", function () {
                preview.hidden = true;
                trigger.focus();
            });
        }
    })();

})();
