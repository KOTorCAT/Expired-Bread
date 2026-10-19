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

    /* --- Подтверждение опасных действий -------------------------------- */

    document.querySelectorAll("form[data-confirm]").forEach(function (form) {
        form.addEventListener("submit", function (event) {
            var message = form.getAttribute("data-confirm");
            if (message && !window.confirm(message)) {
                event.preventDefault();
            }
        });
    });

    /* --- Фильтры: применяем сразу при выборе --------------------------- */

    document.querySelectorAll("[data-auto-submit]").forEach(function (control) {
        control.addEventListener("change", function () {
            if (control.form) control.form.submit();
        });
    });
})();
