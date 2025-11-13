// ===== 智能对话功能 =====
document.getElementById("send_btn").addEventListener("click", async () => {
  const text = document.getElementById("input_text").value.trim();
  const replyArea = document.getElementById("reply_area");

  if (!text) {
    alert("请输入问题");
    return;
  }

  // 显示加载状态
  replyArea.style.display = "block";
  replyArea.className = "result-box loading";
  replyArea.innerHTML = '<span class="status-icon">⏳</span> 正在处理中，请稍候...';

  try {
    const res = await fetch("/chat_with_agent", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text: text, model: "deepseek-chat" }),
    });

    if (!res.ok) {
      throw new Error(`HTTP 错误 ${res.status}`);
    }

    const data = await res.json();
    
    // 解析响应
    let responseText = "无响应";
    if (data.reply) {
      responseText = typeof data.reply === "string" ? data.reply : JSON.stringify(data.reply, null, 2);
    } else if (data.response) {
      responseText = typeof data.response === "string" ? data.response : JSON.stringify(data.response, null, 2);
    }

    replyArea.className = "result-box success";
    replyArea.innerHTML = `<span class="status-icon">✅</span> <strong>助手回复：</strong><br>${escapeHtml(responseText)}`;
    
    // 清空输入框
    document.getElementById("input_text").value = "";
  } catch (err) {
    replyArea.className = "result-box error";
    replyArea.innerHTML = `<span class="status-icon">❌</span> <strong>错误：</strong> ${escapeHtml(err.message)}`;
  }
});

// ===== 模糊测试功能 =====
document.getElementById("fuzz_btn").addEventListener("click", async () => {
  const codeUrl = document.getElementById("code_url").value.trim();
  const email = document.getElementById("email").value.trim();
  const statusEl = document.getElementById("fuzz_status");
  const btn = document.getElementById("fuzz_btn");

  if (!codeUrl) {
    alert("请输入代码仓库地址");
    return;
  }

  if (!email) {
    alert("请输入邮箱地址");
    return;
  }

  // 禁用按钮，显示加载状态
  btn.disabled = true;
  statusEl.style.display = "block";
  statusEl.className = "result-box loading";
  statusEl.innerHTML = '<span class="status-icon">⏳</span> 正在进行模糊测试，请稍候（这可能需要几分钟）...';

  try {
    const res = await fetch("/fuzz_code", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ 
        code_url: codeUrl, 
        email: email,
        model: "deepseek-chat"
      }),
    });

    if (!res.ok) {
      throw new Error(`HTTP 错误 ${res.status}`);
    }

    const data = await res.json();
    
    let message = data.status || "测试完成";
    if (data.response) {
      message += `<br><strong>详情：</strong> ${escapeHtml(JSON.stringify(data.response))}`;
    }

    statusEl.className = "result-box success";
    statusEl.innerHTML = `<span class="status-icon">✅</span> <strong>${escapeHtml(message)}</strong>`;
  } catch (err) {
    statusEl.className = "result-box error";
    statusEl.innerHTML = `<span class="status-icon">❌</span> <strong>测试失败：</strong> ${escapeHtml(err.message)}`;
  } finally {
    btn.disabled = false;
  }
});

// ===== 工具函数 =====
function escapeHtml(text) {
  const map = {
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#039;",
  };
  return text.replace(/[&<>"']/g, (m) => map[m]);
}
