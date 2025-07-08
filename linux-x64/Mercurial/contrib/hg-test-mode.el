;; hg-test-mode.el - Major mode for editing Mercurial tests
;;
;; Copyright 2014 Olivia Mackall <olivia@selenic.com>
;; "I have no idea what I'm doing"
;;
;; This software may be used and distributed according to the terms of the
;; GNU General Public License version 2 or any later version.
;;
;; To enable, add something like the following to your .emacs:
;;
;; (if (file-exists-p "~/hg/contrib/hg-test-mode.el")
;;    (load "~/hg/contrib/hg-test-mode.el"))

(defvar hg-test-mode-hook nil)

(defvar hg-test-mode-map
  (let ((map (make-keymap)))
    (define-key map "\C-j" 'newline-and-indent)
    map)
  "Keymap for hg test major mode")

(add-to-list 'auto-mode-alist '("\\.t\\'" . hg-test-mode))

(defconst hg-test-font-lock-keywords-1
  (list
   '("^  \\(\\$\\|>>>\\) " 1 font-lock-builtin-face)
   '("^  \\(>\\|\\.\\.\\.\\) " 1 font-lock-constant-face)
   '("^  \\([[][0-9]+[]]\\)$" 1 font-lock-warning-face)
   '("^  \\(.*?\\)\\(\\( [(][-a-z]+[)]\\)*\\)$" 1 font-lock-string-face)
   '("\\$?\\(HG\\|TEST\\)\\w+=?" . font-lock-variable-name-face)
   '("^  \\(.*?\\)\\(\\( [(][-a-z]+[)]\\)+\\)$" 2 font-lock-type-face)
   '("^#.*" . font-lock-preprocessor-face)
   '("^\\([^ ].*\\)$" 1 font-lock-comment-face)
   )
  "Minimal highlighting expressions for hg-test mode")

(defvar hg-test-font-lock-keywords hg-test-font-lock-keywords-1
  "Default highlighting expressions for hg-test mode")

(defvar hg-test-mode-syntax-table
  (let ((st (make-syntax-table)))
    (modify-syntax-entry ?\" "w" st) ;; disable standard quoting
    st)
"Syntax table for hg-test mode")

(defun hg-test-mode ()
  (interactive)
  (kill-all-local-variables)
  (use-local-map hg-test-mode-map)
  (set-syntax-table hg-test-mode-syntax-table)
  (set (make-local-variable 'font-lock-defaults) '(hg-test-font-lock-keywords))
  (setq major-mode 'hg-test-mode)
  (setq mode-name "hg-test")
  (run-hooks 'hg-test-mode-hook))

(with-eval-after-load "compile"
  ;; Link to Python sources in tracebacks in .t failures.
  (add-to-list 'compilation-error-regexp-alist-alist
               '(hg-test-output-python-tb
                 "^\\+ +File ['\"]\\([^'\"]+\\)['\"], line \\([0-9]+\\)," 1 2))
  (add-to-list 'compilation-error-regexp-alist 'hg-test-output-python-tb)
  ;; Link to source files in test-check-code.t violations.
  (add-to-list 'compilation-error-regexp-alist-alist
               '(hg-test-check-code-output
                 "\\+  \\([^:\n]+\\):\\([0-9]+\\):$" 1 2))
  (add-to-list 'compilation-error-regexp-alist 'hg-test-check-code-output))

(defun hg-test-mode--test-one-error-line-regexp (test)
  (erase-buffer)
  (setq compilation-locs (make-hash-table))
  (insert (car test))
  (compilation-parse-errors (point-min) (point-max))
  (let ((msg (get-text-property 1 'compilation-message)))
    (should msg)
    (let ((loc (compilation--message->loc msg))
          (line (nth 1 test))
          (file (nth 2 test)))
      (should (equal (compilation--loc->line loc) line))
      (should (equal (caar (compilation--loc->file-struct loc)) file)))
      msg))

(require 'ert)
(ert-deftest hg-test-mode--compilation-mode-support ()
  "Test hg-specific compilation-mode regular expressions"
  (require 'compile)
  (with-temp-buffer
    (font-lock-mode -1)
    (mapc 'hg-test-mode--test-one-error-line-regexp
          '(
            ("+  contrib/debugshell.py:37:" 37 "contrib/debugshell.py")
            ("+    File \"/tmp/hg/mercurial/commands.py\", line 3115, in help_"
             3115 "/tmp/hg/mercurial/commands.py")
            ("+    File \"mercurial/dispatch.py\", line 225, in dispatch"
             225 "mercurial/dispatch.py")))))


(provide 'hg-test-mode)
