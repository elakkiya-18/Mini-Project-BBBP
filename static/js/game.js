document.addEventListener("DOMContentLoaded", function () {

    const modal = document.getElementById("interactionModal");
    const modalIcon = document.getElementById("interactionModalIcon");
    const modalTitle = document.getElementById("interactionModalTitle");
    const modalMessage = document.getElementById("interactionModalMessage");
    const toast = document.getElementById("questToast");

    function closeModal() {
        if (!modal) return;
        modal.classList.remove("is-visible");
        modal.setAttribute("aria-hidden", "true");
        document.body.classList.remove("rq-modal-open");
    }

    function showModal(icon, title, message) {
        if (!modal) return;
        modalIcon.textContent = icon;
        modalTitle.textContent = title;
        modalMessage.textContent = message;
        modal.classList.add("is-visible");
        modal.setAttribute("aria-hidden", "false");
        document.body.classList.add("rq-modal-open");
    }

    function showToast(message, variant = "info") {
        if (!toast) return;
        toast.textContent = message;
        toast.className = `rq-toast is-visible ${variant}`;
        clearTimeout(toast.hideTimer);
        toast.hideTimer = window.setTimeout(() => {
            toast.classList.remove("is-visible");
        }, 2200);
    }

    document.querySelectorAll("[data-password-toggle]").forEach(button => {
        button.addEventListener("click", () => {
            const input = button.parentElement.querySelector("input");
            const showing = input.type === "text";
            input.type = showing ? "password" : "text";
            button.textContent = showing ? "👁" : "🙈";
            button.setAttribute("aria-label", showing ? "Show password" : "Hide password");
            button.setAttribute("aria-pressed", String(!showing));
        });
    });

    document.querySelectorAll("[data-modal-close]").forEach(button => {
        button.addEventListener("click", closeModal);
    });

    if (modal) {
        modal.addEventListener("click", event => {
            if (event.target === modal) closeModal();
        });
    }

    document.addEventListener("keydown", event => {
        if (event.key === "Escape") closeModal();
    });

    const splash = document.getElementById("welcomeSplash");
    const enterQuest = document.getElementById("enterQuest");

    if (splash && !sessionStorage.getItem("respectQuestSplashSeen")) {
        window.setTimeout(() => {
            splash.classList.add("is-visible");
            splash.setAttribute("aria-hidden", "false");
        }, 250);
    }

    function closeSplash() {
        if (!splash) return;
        splash.classList.remove("is-visible");
        splash.setAttribute("aria-hidden", "true");
        sessionStorage.setItem("respectQuestSplashSeen", "true");
    }

    if (enterQuest) enterQuest.addEventListener("click", closeSplash);
    if (splash) {
        splash.addEventListener("click", event => {
            if (event.target === splash) closeSplash();
        });
    }

    const cards = Array.from(document.querySelectorAll(".question-card"));

    if (!cards.length) return;

    const form = document.getElementById("quizForm");
    const resumeIndex = Number(form?.dataset.resumeIndex || 0);
    let current = Number.isNaN(resumeIndex) ? 0 : resumeIndex;

    const counter = document.getElementById("questionCounter");
    const progress = document.getElementById("quizProgress");

    function updateProgress(index, answered = index) {
        if (counter) {
            counter.textContent = `Question ${index + 1} / ${cards.length}`;
        }

        if (progress) {
            const percent = Math.round((answered / cards.length) * 100);
            progress.style.width = percent + "%";
            progress.textContent = percent + "%";
        }
    }

    function showQuestion(index) {
        cards.forEach((card, i) => {
            card.classList.toggle("d-none", i !== index);
        });

        current = index;
        updateProgress(index);
        saveProgress(index);
    }

    function saveProgress(index) {
        if (!form) return;
        const match = form.action.match(/\/submit\/(\d+)/);
        if (!match) return;
        const answers = {};
        form.querySelectorAll("input[type=radio]:checked").forEach(input => {
            answers[input.name.replace("q_", "")] = input.value;
        });
        fetch(`/quiz/${match[1]}/progress`, {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({current_index: index, answers})
        }).catch(() => {
            showToast("Progress could not be saved. Please check your connection.", "warning");
        });
    }

    function markSelectedOption(card) {
        const labels = card.querySelectorAll(".option-card");
        const selectedInput = card.querySelector("input[type=radio]:checked");

        labels.forEach(label => {
            const input = label.querySelector("input[type=radio]");
            label.classList.toggle("selected", selectedInput && input === selectedInput);
        });
    }

    cards.forEach(card => {
        card.querySelectorAll("input[type=radio]").forEach(input => {
            input.addEventListener("change", () => {
                markSelectedOption(card);
                saveProgress(current);
            });
        });
    });

    // -----------------------------
    // CHECK ANSWER BUTTON
    // -----------------------------
    document.querySelectorAll(".check-btn").forEach(btn => {

        btn.addEventListener("click", function () {

            const card = cards[current];
            const selected = card.querySelector("input[type=radio]:checked");

            if (!selected) {
                showModal("📝", "Choose an answer", "Please select an answer before continuing.");
                return;
            }

            const correct = card.dataset.correct;
            const learning = card.dataset.learning;
            const rating = Number(card.dataset[`rating${selected.value}`] || 1);

            const ratingBox = card.querySelector(".answer-rating-box");
            const ratingTitle = card.querySelector(".answer-rating-title");
            const ratingStars = card.querySelector(".answer-rating-stars");
            const explanation = card.querySelector(".answer-rating-explanation");
            const learningPoint = card.querySelector(".bbbp-box");
            const optionLabels = card.querySelectorAll(".option-card");
            const isCorrect = selected.value === correct;

            ratingBox.classList.remove("d-none");
            ratingTitle.textContent = `${rating}-star answer`;
            ratingStars.textContent = "★".repeat(rating) + "☆".repeat(5 - rating);
            ratingStars.classList.toggle("is-correct", isCorrect);
            ratingStars.setAttribute(
                "aria-label",
                `${rating} out of 5 stars`
            );

            optionLabels.forEach(label => {
                const input = label.querySelector("input[type=radio]");
                if (!input) return;
                const isAnswerCorrect = input.value === correct;
                const isSelected = input === selected;
                label.classList.toggle("correct", isAnswerCorrect && !input.disabled);
                label.classList.toggle("wrong", isSelected && !isCorrect);
            });

            if (isCorrect) {
                learningPoint.classList.add("d-none");
                showModal("⭐", `${rating}-star answer`, "You chose the correct answer.");
                showToast(`${rating} stars  +${rating * 2} points`, "success");
            } else {
                explanation.textContent = learning || "";
                learningPoint.classList.remove("d-none");
                showModal("⭐", `${rating}-star answer`, learning || "Review the learning point to try again next time.");
                showToast(`${rating} stars  +${rating * 2} points`, "warning");
            }

            updateProgress(current, current + 1);

            const submittedAnswer = document.createElement("input");
            submittedAnswer.type = "hidden";
            submittedAnswer.name = selected.name;
            submittedAnswer.value = selected.value;
            submittedAnswer.className = "submitted-answer";
            card.appendChild(submittedAnswer);

            card.querySelectorAll("input[type=radio]").forEach(r => {
                r.disabled = true;
            });

            this.classList.add("d-none");
            const nextButton = card.querySelector(".next-btn");
            if (nextButton) nextButton.classList.remove("d-none");
            saveProgress(current);
        });

    });

    // -----------------------------
    // NEXT BUTTON
    // -----------------------------
    document.querySelectorAll(".next-btn").forEach(btn => {

        btn.addEventListener("click", function (event) {

            if (current < cards.length - 1) {
                showQuestion(current + 1);
                return;
            }

            event.preventDefault();
            const form = document.getElementById("quizForm");
            if (form) {
                showModal("🏆", "Level complete!", "Your answers are ready. Submit the level to save your score.");
                window.setTimeout(() => form.requestSubmit(), 700);
            }

        });

    });

    // -----------------------------
    // PREVIOUS BUTTON
    // -----------------------------
    document.querySelectorAll(".prev-btn").forEach(btn => {

        btn.addEventListener("click", function () {

            if (current > 0) {
                showQuestion(current - 1);
            }

        });

    });

    if (form) {
        const levelKey = `respectQuestLevelIntro_${form.action.match(/\/quiz\/(\d+)/)?.[1] || "current"}`;
        if (!sessionStorage.getItem(levelKey)) {
            showModal("🚀", "Level challenge unlocked!", "Read each question carefully and tap through the quest to unlock your next milestone.");
            sessionStorage.setItem(levelKey, "true");
        }

        form.addEventListener("submit", function (event) {
            for (const card of cards) {
                const selected = card.querySelector("input[type=radio]:checked");

                if (!selected) {
                    event.preventDefault();
                    showModal("⏳", "Almost there!", "Please answer all 10 questions before submitting.");
                    return;
                }
            }
        });
    }

    showQuestion(current);

});