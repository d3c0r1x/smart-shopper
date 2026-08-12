import React from "react";
import ReactDOM from "react-dom/client";
import { init, mockTelegramEnv, retrieveLaunchParams } from "@telegram-apps/sdk";
import App from "./App";
import "./styles.css";

// В браузере (без Telegram) запускаемся в демо-режиме — это позволяет
// открыть Mini App локально:  npm run dev  →  http://localhost:5173
// Тема в демо — светлая; в Telegram тема наследуется из themeParams.
const demoLaunchParams = () => {
  const initData = new URLSearchParams({
    user: JSON.stringify({
      id: 1,
      first_name: "Demo",
      last_name: "Shopper",
      username: "demo",
      language_code: "ru",
    }),
    auth_date: String(Math.floor(Date.now() / 1000)),
    signature: "89d6079ad676d1f57d6f5d0b2b0a1a1a89d6079ad676d1f57d6f5d0b2b0a1a1a",
    hash: "89d6079ad676d1f57d6f5d0b2b0a1a1a89d6079ad676d1f57d6f5d0b2b0a1a1a",
  });
  return new URLSearchParams({
    tgWebAppData: initData as unknown as string,
    tgWebAppVersion: "8.0",
    tgWebAppPlatform: "macos",
    tgWebAppThemeParams: JSON.stringify({
      bg_color: "#ffffff",
      text_color: "#000000",
      hint_color: "#8a8f98",
      link_color: "#2481cc",
      button_color: "#2481cc",
      button_text_color: "#ffffff",
      secondary_bg_color: "#f0f2f5",
    }),
  });
};

try {
  retrieveLaunchParams();
} catch {
  // Вне Telegram: сбрасываем устаревшие моки из sessionStorage и ставим свои.
  sessionStorage.clear();
  mockTelegramEnv({ launchParams: demoLaunchParams(), resetPostMessage: true });
}
init();

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
