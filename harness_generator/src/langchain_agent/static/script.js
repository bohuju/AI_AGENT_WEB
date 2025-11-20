// ...existing code...
// 专用于代码模糊测试页面的脚本

function showFieldError(id, msg) {
  const el = document.getElementById(id);
  if (!el) return;
  el.textContent = msg;
  el.style.display = msg ? 'block' : 'none';
}

function validateEmail(email) {
  // 简单邮箱校验
  const re = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
  return re.test(email);
}

// 高级参数面板切换（下拉动画）
const toggleBtn = document.getElementById("toggle_adv");
const advPanel = document.getElementById("adv_panel");
toggleBtn.addEventListener("click", () => {
  const open = advPanel.classList.toggle("open");
  advPanel.setAttribute("aria-hidden", (!open).toString());
  toggleBtn.setAttribute("aria-expanded", open.toString());
  toggleBtn.textContent = open ? "隐藏高级参数 ▴" : "显示高级参数 ▾";
});

document.getElementById("clear_btn").addEventListener("click", () => {
  document.getElementById("code_url").value = "";
  document.getElementById("email").value = "";
  document.getElementById("temperature").value = "";
  document.getElementById("timeout").value = "";
  document.getElementById("fuzz_duration").value = "";
  document.getElementById("max_tokens").value = "";
  showFieldError("code_url_err", "");
  showFieldError("email_err", "");
  showFieldError("api_key_err", ""); // 若文件中存在兼容旧 id，这里不报错
  const status = document.getElementById("fuzz_status");
  status.style.display = "none";
  status.className = "result-box";
  status.innerHTML = "";
  // 收起高级参数
  if (advPanel.classList.contains("open")) {
    advPanel.classList.remove("open");
    advPanel.setAttribute("aria-hidden", "true");
    toggleBtn.setAttribute("aria-expanded", "false");
    toggleBtn.textContent = "显示高级参数 ▾";
  }
});

document.getElementById("fuzz_btn").addEventListener("click", async () => {
  const codeUrl = document.getElementById("code_url").value.trim();
  const email = document.getElementById("email").value.trim();

  // 模型
  const modelEls = document.getElementsByName("model");
  let model = null;
  for (const el of modelEls) { if (el.checked) { model = el.value; break; } }

  // 高级参数（可选）——使用默认值当输入为空或无效时
  const temperatureInput = parseFloat(document.getElementById("temperature").value);
  const timeoutInput = parseInt(document.getElementById("timeout").value, 10);
  const fuzzDurationInput = parseInt(document.getElementById("fuzz_duration").value, 10);
  const maxTokensInput = parseInt(document.getElementById("max_tokens").value, 10);
  const roundsInput = parseInt(document.getElementById("rounds").value, 10);

  const temperature = Number.isFinite(temperatureInput) ? temperatureInput : 0.5;
  const timeout = Number.isInteger(timeoutInput) && timeoutInput > 0 ? timeoutInput : 10;
  const fuzz_duration = Number.isInteger(fuzzDurationInput) && fuzzDurationInput > 0 ? fuzzDurationInput : 5;
  const max_tokens = Number.isInteger(maxTokensInput) && maxTokensInput > 0 ? maxTokensInput : 1024;
  const rounds = Number.isInteger(roundsInput) && roundsInput > 0 ? roundsInput : 5;
  let valid = true;
  // 校验并显示错误
  if (!codeUrl) { showFieldError("code_url_err", "代码仓库地址为必填"); valid = false; } else { showFieldError("code_url_err", ""); }
  if (!email || !validateEmail(email)) { showFieldError("email_err", "请输入有效邮箱"); valid = false; } else { showFieldError("email_err", ""); }
  if (!model) {
    alert("请选择模型");
    valid = false;
  }

  if (!valid) {
    return;
  }

  const statusEl = document.getElementById("fuzz_status");
  const btn = document.getElementById("fuzz_btn");
  btn.disabled = true;
  statusEl.style.display = "block";
  statusEl.className = "result-box loading";
  statusEl.innerHTML = '<span class="status-icon">⏳</span> 正在进行模糊测试，请耐心等待...';

  try {
    const res = await fetch("/fuzz_code", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        code_url: codeUrl,
        email: email,
        model: model,
        // 可选高级参数
        temperature: temperature,
        timeout: timeout,
        time_budget: fuzz_duration,
        max_tokens: max_tokens,
        rounds: rounds
      })
    });

    if (!res.ok) {
      throw new Error(`HTTP 错误 ${res.status}`);
    }

    const data = await res.json();

    if (data.status && data.status !== "error") {
      statusEl.className = "result-box success";
      statusEl.innerHTML = `<span class="status-icon">✅</span> <strong>${escapeHtml(data.status)}</strong>`;
      if (data.message) {
        statusEl.innerHTML += `<div style="margin-top:8px;color:#155724">${escapeHtml(data.message)}</div>`;
      }
    } else {
      statusEl.className = "result-box error";
      const msg = data.message || "测试失败";
      statusEl.innerHTML = `<span class="status-icon">❌</span> <strong>测试失败：</strong> ${escapeHtml(msg)}`;
    }
  } catch (err) {
    statusEl.className = "result-box error";
    statusEl.innerHTML = `<span class="status-icon">❌</span> <strong>错误：</strong> ${escapeHtml(err.message)}`;
  } finally {
    btn.disabled = false;
  }
});

// ===== 工具函数 =====
function escapeHtml(text) {
  if (!text) return "";
  return String(text).replace(/[&<>"']/g, function (m) {
    return ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#039;' })[m];
  });
}
// ...existing code...