"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";

import {
  Divider,
  Field,
  FormMessage,
  GoogleButton,
  SubmitButton,
  validateEmail,
  validatePassword,
} from "@/components/auth/auth-form";
import { AuthShell } from "@/components/auth/auth-shell";
import { friendlyAuthError, getSupabase } from "@/lib/supabase";

export default function SignUpPage() {
  const router = useRouter();
  const [form, setForm] = useState({
    name: "",
    email: "",
    password: "",
    confirm: "",
  });
  const [errors, setErrors] = useState<Record<string, string | undefined>>({});
  const [formError, setFormError] = useState<string | null>(null);
  const [verifyNotice, setVerifyNotice] = useState(false);
  const [pending, setPending] = useState(false);

  const set = (key: keyof typeof form) => (e: React.ChangeEvent<HTMLInputElement>) =>
    setForm((current) => ({ ...current, [key]: e.target.value }));

  async function onSubmit(event: React.FormEvent) {
    event.preventDefault();
    const nextErrors = {
      name: form.name.trim() ? undefined : "Enter your name.",
      email: validateEmail(form.email),
      password: validatePassword(form.password),
      confirm:
        form.confirm === form.password ? undefined : "Passwords do not match.",
    };
    setErrors(nextErrors);
    if (Object.values(nextErrors).some(Boolean)) return;

    setPending(true);
    setFormError(null);
    const { data, error } = await getSupabase().auth.signUp({
      email: form.email.trim(),
      password: form.password,
      options: {
        data: { name: form.name.trim() },
        emailRedirectTo: `${window.location.origin}/auth/callback`,
      },
    });
    setPending(false);

    if (error) {
      setFormError(friendlyAuthError(error.message));
      return;
    }
    // With email confirmation on, Supabase returns a user but no session --
    // the account is not usable until the link is clicked, so say so instead
    // of dropping them on a dashboard that would immediately 401.
    if (!data.session) {
      setVerifyNotice(true);
      return;
    }
    router.replace("/dashboard/knowledge");
  }

  async function onGoogle() {
    setPending(true);
    setFormError(null);
    const { error } = await getSupabase().auth.signInWithOAuth({
      provider: "google",
      options: { redirectTo: `${window.location.origin}/auth/callback` },
    });
    if (error) {
      setPending(false);
      setFormError(friendlyAuthError(error.message));
    }
  }

  return (
    <AuthShell
      title="Create your account"
      subtitle="Your own knowledge base, with 5 GB to fill."
      footer={
        <>
          Already have an account?{" "}
          <Link href="/login" className="font-medium text-neutral-900 underline underline-offset-4">
            Sign in
          </Link>
        </>
      }
    >
      {verifyNotice ? (
        <FormMessage kind="success">
          Check {form.email} for a confirmation link, then sign in.
        </FormMessage>
      ) : (
        <form onSubmit={onSubmit} noValidate className="space-y-4">
          {formError ? <FormMessage kind="error">{formError}</FormMessage> : null}

          <Field
            label="Name"
            autoComplete="name"
            value={form.name}
            onChange={set("name")}
            error={errors.name}
          />
          <Field
            label="Email"
            type="email"
            autoComplete="email"
            value={form.email}
            onChange={set("email")}
            error={errors.email}
          />
          <Field
            label="Password"
            type="password"
            autoComplete="new-password"
            value={form.password}
            onChange={set("password")}
            error={errors.password}
          />
          <Field
            label="Confirm password"
            type="password"
            autoComplete="new-password"
            value={form.confirm}
            onChange={set("confirm")}
            error={errors.confirm}
          />

          <SubmitButton pending={pending}>Create account</SubmitButton>
          <Divider />
          <GoogleButton onClick={onGoogle} pending={pending} />
        </form>
      )}
    </AuthShell>
  );
}
