
import { createRoot } from "react-dom/client";
import App from "./app/App.tsx";
import "./styles/index.css";

// Force dark theme tokens so component defaults don't render dark text on dark UI.
document.documentElement.classList.add("dark");

createRoot(document.getElementById("root")!).render(<App />);
  
