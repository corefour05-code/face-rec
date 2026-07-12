/* Shared face-capture widget: oval-guided camera box + step pills + thumbnail
 * grid + hidden data-URL inputs, reused by Add/Edit Student and Add/Edit Faculty.
 */
class FaceCaptureWidget {
  constructor(opts) {
    this.video = opts.video;
    this.captureBtn = opts.captureBtn;
    this.counterEl = opts.counterEl;
    this.stepListEl = opts.stepListEl;
    this.thumbGridEl = opts.thumbGridEl;
    this.hiddenContainer = opts.hiddenContainer;
    this.inputPrefix = opts.inputPrefix || "photo_";
    this.total = opts.totalShots;
    this.labels = opts.stepLabels;
    this.statusEl = opts.statusEl;
    this.onComplete = opts.onComplete || (() => {});
    this.captured = [];
    this.canvas = document.createElement("canvas");
    this.started = false;

    this._renderSteps();
    this._updateCounter();
    this.captureBtn.addEventListener("click", () => this.captureShot());
  }

  async start() {
    if (this.started) return;
    const stream = await navigator.mediaDevices.getUserMedia({
      video: { width: 640, height: 480 },
      audio: false,
    });
    this.video.srcObject = stream;
    await new Promise((resolve) => (this.video.onloadedmetadata = resolve));
    this.started = true;
  }

  stop() {
    if (this.video.srcObject) {
      this.video.srcObject.getTracks().forEach((t) => t.stop());
      this.video.srcObject = null;
    }
    this.started = false;
  }

  _renderSteps() {
    this.stepListEl.innerHTML = "";
    this.labels.forEach((label) => {
      const pill = document.createElement("span");
      pill.className = "step-pill";
      pill.textContent = label;
      this.stepListEl.appendChild(pill);
    });
    this._highlightSteps();
  }

  _highlightSteps() {
    [...this.stepListEl.children].forEach((pill, i) => {
      pill.classList.remove("active", "done");
      if (i < this.captured.length) pill.classList.add("done");
      else if (i === this.captured.length) pill.classList.add("active");
    });
  }

  _updateCounter() {
    this.counterEl.textContent = `${this.captured.length}/${this.total}`;
  }

  async captureShot() {
    if (this.captured.length >= this.total) return;
    this.canvas.width = this.video.videoWidth || 640;
    this.canvas.height = this.video.videoHeight || 480;
    const ctx = this.canvas.getContext("2d");
    ctx.drawImage(this.video, 0, 0, this.canvas.width, this.canvas.height);

    const blob = await new Promise((resolve) => this.canvas.toBlob(resolve, "image/jpeg", 0.9));
    const formData = new FormData();
    formData.append("image", blob, "shot.jpg");

    this.captureBtn.disabled = true;
    try {
      const res = await fetch("/api/capture/validate", { method: "POST", body: formData });
      const data = await res.json();
      if (!data.ok) {
        this._setStatus(`Rejected: ${data.reason}`, true);
        return;
      }
      const dataUrl = this.canvas.toDataURL("image/jpeg", 0.9);
      this.captured.push(dataUrl);
      this._addHiddenInput(this.captured.length, dataUrl);
      this._addThumbnail(dataUrl);
      this._updateCounter();
      this._highlightSteps();
      if (this.captured.length >= this.total) {
        this._setStatus("All photos captured!", false);
        this.onComplete();
      } else {
        this._setStatus(`Captured (sharpness=${data.sharpness.toFixed(0)})`, false);
      }
    } catch (e) {
      this._setStatus(`Error: ${e}`, true);
    } finally {
      this.captureBtn.disabled = this.captured.length >= this.total;
    }
  }

  _addHiddenInput(n, dataUrl) {
    let input = this.hiddenContainer.querySelector(`input[name="${this.inputPrefix}${n}"]`);
    if (!input) {
      input = document.createElement("input");
      input.type = "hidden";
      input.name = `${this.inputPrefix}${n}`;
      this.hiddenContainer.appendChild(input);
    }
    input.value = dataUrl;
  }

  _addThumbnail(dataUrl) {
    const img = document.createElement("img");
    img.src = dataUrl;
    img.className = "capture-thumb";
    this.thumbGridEl.appendChild(img);
  }

  _setStatus(msg, isErr) {
    if (!this.statusEl) return;
    this.statusEl.textContent = msg;
    this.statusEl.className = isErr ? "status err" : "status ok";
  }

  isComplete() {
    return this.captured.length >= this.total;
  }

  reset() {
    this.captured = [];
    this.hiddenContainer.innerHTML = "";
    this.thumbGridEl.innerHTML = "";
    this._updateCounter();
    this._highlightSteps();
    this.captureBtn.disabled = false;
  }
}
