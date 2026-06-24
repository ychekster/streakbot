/** Точка входа фронтенда: монтирует приложение и подключает глобальные стили. */

import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { App } from "./App";
// Порядок важен: сначала дизайн-токены (переменные), затем глобальные стили.
import "./styles/variables.css";
import "./styles/global.css";

const container = document.getElementById("root");
if (!container) {
  throw new Error("Корневой элемент #root не найден в index.html");
}

createRoot(container).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
