# New knowledge

- **marked**: a small JavaScript library that turns Markdown text into HTML. The model replies in Markdown (`## Heading`, `- item`, fenced code blocks), and marked converts it into real HTML elements (`<h2>`, `<ul>`, `<pre><code>`) so the reply is shown formatted instead of as raw symbols. It is one file with no dependencies and no build step.
- **DOMPurify**: a small JavaScript library that cleans HTML before it is inserted into the page. Model output is untrusted, so it might contain `<script>` tags, `onerror=` attributes or `javascript:` links. Inserting that unchecked into the page would run code in the user's browser (XSS). DOMPurify removes anything dangerous and keeps safe formatting.
- **How they work together**: model text → `marked.parse()` → HTML → `DOMPurify.sanitize()` → safe HTML → `element.innerHTML`. marked handles the formatting and DOMPurify handles the safety. Neither one is enough without the other.
