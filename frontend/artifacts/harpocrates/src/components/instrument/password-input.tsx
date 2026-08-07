import { useState } from "react";
import { Eye, EyeOff, KeyRound } from "lucide-react";

interface PasswordInputProps {
  value: string;
  onChange: (value: string) => void;
  label: string;
  testId: string;
  placeholder?: string;
}

function PasswordInput({ value, onChange, label, testId, placeholder = "Optional — add a private key" }: PasswordInputProps) {
  const [visible, setVisible] = useState(false);
  return (
    <label className="field-label">
      {label}
      <div className="password-wrap">
        <KeyRound size={15} />
        <input
          type={visible ? "text" : "password"}
          value={value}
          onChange={(event) => onChange(event.target.value)}
          placeholder={placeholder}
          data-testid={testId}
        />
        <button
          type="button"
          onClick={() => setVisible((show) => !show)}
          aria-label={visible ? "Hide password" : "Show password"}
          data-testid={`${testId}-toggle`}
        >
          {visible ? <EyeOff size={16} /> : <Eye size={16} />}
        </button>
      </div>
    </label>
  );
}

export { PasswordInput, type PasswordInputProps };
