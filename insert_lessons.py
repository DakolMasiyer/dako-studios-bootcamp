#!/usr/bin/env python3
"""Insert all remaining lesson HTML into the bootcamp DB."""
import sqlite3
from pathlib import Path

DB = Path("data/bootcamp.db")

LESSONS = {
6: ("""<h3>What You Will Learn</h3>
<ul>
  <li>What the internet is and how data travels between computers</li>
  <li>What a browser is and how URLs work</li>
  <li>How to navigate websites, use bookmarks, and stay safe online</li>
</ul>

<h3>Core Concepts</h3>
<p>The internet is not a place — it is a <strong>global network of computers</strong> connected by cables, satellite signals, and radio waves. When you load a webpage, your computer sends a tiny request that travels to another computer (called a <em>server</em>) that holds that page, and the server sends it back in fractions of a second.</p>
<p>Think of it like the <strong>mobile money network</strong>. When you send money via M-Pesa or MTN Mobile Money, you do not physically carry cash across town. Your phone sends a request through a network, the system processes it, and the recipient gets their money. The internet works on the same principle — requests and responses moving through infrastructure you never see.</p>
<p><strong>Browsers and URLs</strong></p>
<p>A <em>browser</em> is the software you use to access the internet — Chrome, Firefox, Edge, and Safari are the most common. The address bar is where you type a <em>URL</em> (Uniform Resource Locator) — the unique address of a webpage.</p>
<p>A URL has parts: <em>https://</em> (the protocol — tells the browser to connect securely), the <em>domain name</em> (e.g., wikipedia.org — the website identity), and optionally a <em>path</em> that points to a specific page. The padlock icon in the address bar means the connection is <strong>encrypted</strong> (secure). Always look for it before entering personal information.</p>
<p><strong>Bookmarks</strong></p>
<p>A bookmark saves a page URL so you can return to it instantly — like folding the corner of a book page. Use them to build a personal library of useful sites.</p>

<h3>Key Terms</h3>
<dl>
  <dt>Internet</dt>
  <dd>A worldwide network of connected computers that exchange data using agreed communication rules called protocols.</dd>
  <dt>Browser</dt>
  <dd>Software that retrieves and displays web pages — examples include Google Chrome, Mozilla Firefox, and Microsoft Edge.</dd>
  <dt>URL (Uniform Resource Locator)</dt>
  <dd>The unique address of a specific page on the internet, for example: https://www.wikipedia.org.</dd>
  <dt>HTTPS</dt>
  <dd>A secure web protocol. The S stands for Secure — data between your browser and the website is encrypted, protecting it from interception.</dd>
</dl>

<h3>Step-by-Step Example</h3>
<p>Visiting and bookmarking Wikipedia:</p>
<ol>
  <li>Open your browser (click the Chrome or Firefox icon).</li>
  <li>Click the <strong>address bar</strong> at the top. Type <strong>https://www.wikipedia.org</strong> and press <strong>Enter</strong>.</li>
  <li>Notice the <strong>padlock icon</strong> on the left of the address — the connection is secure.</li>
  <li>In the search box, type <strong>Computer Network</strong> and press Enter.</li>
  <li>Bookmark this page: click the <strong>star icon</strong> in the address bar (or press <strong>Ctrl + D</strong>). Click <em>Done</em>.</li>
  <li>Close the tab, then click your bookmarks bar to return to the page instantly.</li>
</ol>

<h3>Before You Start the Mission</h3>
<p>You now understand how data moves across the internet, what a browser does, and how to navigate using URLs. In your mission you will navigate to Wikipedia by typing its URL directly, search for a specific topic, and save the page as a bookmark. These are foundational habits every digital professional uses daily.</p>""",
"how does the internet work for beginners explained simply"),

7: ("""<h3>What You Will Learn</h3>
<ul>
  <li>How to construct precise search queries that return the results you actually need</li>
  <li>How to evaluate whether a search result is reliable and trustworthy</li>
  <li>How to record and organise the information you find</li>
</ul>

<h3>Core Concepts</h3>
<p>Most people use Google every day, but few use it well. Typing a vague phrase like <em>news about Africa</em> returns millions of results. Typing a precise query like <em>"renewable energy" Nigeria 2024 site:bbc.com</em> returns exactly what you need. The difference is <strong>search skill</strong>.</p>
<p>Think of a search engine like a very large library assistant. If you walk in and say "I want something interesting", the assistant is lost. But if you say "I need the most recent book about solar energy in West Africa", they can find it in seconds. Your job is to be specific.</p>
<p><strong>Building Better Queries</strong></p>
<ul>
  <li><strong>Use specific keywords</strong> — instead of <em>capital city Ghana</em>, try <em>Ghana capital city name</em>.</li>
  <li><strong>Use quotes for exact phrases</strong> — searching <em>"longest river in Africa"</em> finds pages with that exact phrase.</li>
  <li><strong>Add a year</strong> — <em>Nigeria population 2025</em> filters results to current data.</li>
  <li><strong>Use reliable domains</strong> — results from .gov, .edu, or established news sites (bbc.com, reuters.com) are generally more trustworthy than unknown blogs.</li>
</ul>
<p><strong>Evaluating Results</strong></p>
<p>Not everything on the internet is true. Before trusting information, ask: Who wrote this? When was it written? Does another reputable source say the same thing? If two or three credible sites agree, you can be more confident in the information.</p>

<h3>Key Terms</h3>
<dl>
  <dt>Search Engine</dt>
  <dd>A tool that indexes billions of web pages and retrieves the most relevant ones based on your query (e.g., Google, DuckDuckGo, Bing).</dd>
  <dt>Search Query</dt>
  <dd>The words or phrase you type into a search engine to find information.</dd>
  <dt>Keyword</dt>
  <dd>A specific, important word included in your search query that helps the engine find relevant results.</dd>
  <dt>Source Credibility</dt>
  <dd>How trustworthy and accurate a source of information is, based on who published it, when, and whether it is supported by evidence.</dd>
</dl>

<h3>Step-by-Step Example</h3>
<p>Finding verified facts for a document:</p>
<ol>
  <li>Open your browser and go to <strong>google.com</strong> or <strong>duckduckgo.com</strong>.</li>
  <li>In the search bar, type: <strong>Ghana capital city</strong> and press Enter.</li>
  <li>Read the top result. Note the source — is it a government site, an encyclopaedia, or an unknown blog?</li>
  <li>Open a new document (Ctrl + N in your word processor) and write the answer with the source URL.</li>
  <li>Repeat for: <strong>longest river in Africa</strong> and <strong>Nigeria population 2025</strong>.</li>
  <li>Save the document as <strong>search_results.txt</strong> in your Bootcamp Projects folder.</li>
</ol>

<h3>Before You Start the Mission</h3>
<p>You now know how to construct targeted search queries and evaluate the quality of results. In your mission you will execute three specific searches, record the verified answers in a document, and save it — building a habit of organised, evidence-based research that will serve you in every digital task ahead.</p>""",
"google search tips for beginners advanced search techniques"),

8: ("""<h3>What You Will Learn</h3>
<ul>
  <li>How to create or access a Gmail account</li>
  <li>The anatomy of a professional email: subject line, greeting, body, sign-off</li>
  <li>How to compose and send your first professional email</li>
</ul>

<h3>Core Concepts</h3>
<p>Email is the standard form of professional written communication in almost every industry. Whether you are applying for a job, communicating with a client, or following up on an assignment, a well-written email makes a strong impression. A poorly written one can close doors before you even knock.</p>
<p>Think of an email like a <strong>formal letter</strong>, but delivered instantly. Like any letter, it has a structure — and following that structure signals that you are professional and take your communication seriously.</p>
<p><strong>Parts of a Professional Email</strong></p>
<ul>
  <li><strong>To:</strong> The recipient email address. Double-check this before sending — one wrong character and your message goes nowhere (or to the wrong person).</li>
  <li><strong>Subject line:</strong> A brief, specific summary of what the email is about. <em>Introduction from Dako Bootcamp Student</em> is good. <em>Hi</em> is not.</li>
  <li><strong>Greeting:</strong> Start with <em>Dear [Name],</em> or <em>Good morning [Name],</em>. Avoid <em>Hey</em> in professional contexts.</li>
  <li><strong>Body:</strong> State your purpose in the first sentence. Keep paragraphs short (2-4 sentences). One topic per paragraph.</li>
  <li><strong>Sign-off:</strong> End with <em>Kind regards,</em> or <em>Best regards,</em> followed by your full name.</li>
</ul>
<p><strong>Gmail Basics</strong></p>
<p>Gmail (mail.google.com) is the most widely used email service. The interface shows your <em>Inbox</em> on the left (incoming messages), a <em>Compose</em> button to write new emails, and folders like <em>Sent</em> (emails you sent), <em>Drafts</em> (unfinished emails), and <em>Spam</em> (suspicious messages the system filtered).</p>

<h3>Key Terms</h3>
<dl>
  <dt>Email Address</dt>
  <dd>A unique identifier for sending and receiving electronic messages, in the format name@domain.com (e.g., amara@gmail.com).</dd>
  <dt>Subject Line</dt>
  <dd>A brief title for your email that tells the recipient what the message is about before they open it.</dd>
  <dt>Inbox</dt>
  <dd>The folder in your email client where incoming messages are delivered and stored.</dd>
  <dt>CC (Carbon Copy)</dt>
  <dd>A field where you can add extra recipients who should receive a copy of the email, without being the main addressee.</dd>
</dl>

<h3>Step-by-Step Example</h3>
<p>Composing a professional introductory email in Gmail:</p>
<ol>
  <li>Go to <strong>mail.google.com</strong> and sign in (or create an account if you do not have one).</li>
  <li>Click the <strong>Compose</strong> button (bottom left). A new message window opens.</li>
  <li>In the <strong>To:</strong> field, type your own email address (you are sending to yourself as practice).</li>
  <li>In the <strong>Subject:</strong> field, type: <em>Bootcamp Introduction — [Your Name]</em></li>
  <li>In the body, type a proper greeting, introduce yourself, state your bootcamp start date, and mention one learning goal. End with <em>Kind regards,</em> and your name.</li>
  <li>Click <strong>Send</strong>. Check your <em>Sent</em> folder to confirm it was delivered.</li>
</ol>

<h3>Before You Start the Mission</h3>
<p>You now understand email structure and Gmail navigation. In your mission you will write a real professional introductory email — with a clear subject line, proper greeting, and a sign-off — and take a screenshot of it in your Sent folder as proof. Treat every email you write from now on as a professional document.</p>""",
"how to write a professional email for beginners gmail tutorial"),

9: ("""<h3>What You Will Learn</h3>
<ul>
  <li>How to write emails with the right tone for different situations</li>
  <li>How to attach a file to an email and send it successfully</li>
  <li>Best practices for professional email etiquette that make you stand out</li>
</ul>

<h3>Core Concepts</h3>
<p>Sending an email is easy. Sending an email that is clear, appropriate, and professional is a skill. Today you go deeper — learning tone, attachments, and the habits that separate effective digital communicators from everyone else.</p>
<p><strong>Tone and Etiquette</strong></p>
<p>Tone in writing is like tone of voice in speaking. You would not shout in a quiet office or whisper at a market. Match your email tone to the context:</p>
<ul>
  <li><strong>Formal</strong> (to employers, government, teachers): Full sentences, proper titles, no slang, no abbreviations like <em>pls</em> or <em>thx</em>.</li>
  <li><strong>Semi-formal</strong> (to colleagues or classmates): Friendly but clear. Still use complete sentences.</li>
  <li><strong>Avoid ALL CAPS</strong> — it reads as shouting.</li>
  <li><strong>Proofread before sending</strong> — one spelling error can undermine an otherwise strong message. Read it aloud once before clicking Send.</li>
</ul>
<p><strong>Sending Attachments</strong></p>
<p>An attachment is a file (document, image, PDF) you send alongside your email. Think of it like an envelope with a letter inside — the email is the letter, the attachment is the extra document you staple to it.</p>
<p>Rules for attachments:</p>
<ul>
  <li><strong>Always mention the attachment in the email body</strong> — write <em>Please find my biography attached.</em> Never send an attachment with no explanation.</li>
  <li><strong>Name your file clearly</strong> before attaching (e.g., <em>Amara_Diallo_Biography.docx</em>, not <em>document1.docx</em>).</li>
  <li><strong>Check the file size</strong> — most email providers have a 25MB attachment limit. Large files should use cloud storage links instead.</li>
</ul>

<h3>Key Terms</h3>
<dl>
  <dt>Attachment</dt>
  <dd>A file (document, image, spreadsheet, etc.) added to an email and sent alongside the message.</dd>
  <dt>Email Etiquette</dt>
  <dd>The accepted rules and norms for writing professional, respectful, and effective emails.</dd>
  <dt>Reply vs Reply All</dt>
  <dd>Reply sends your response only to the sender. Reply All sends it to every person on the original email. Use Reply All only when everyone needs to see your response.</dd>
  <dt>Signature</dt>
  <dd>A block of text automatically added to the bottom of your emails containing your name, title, and contact information.</dd>
</dl>

<h3>Step-by-Step Example</h3>
<p>Sending a professional email with an attachment:</p>
<ol>
  <li>Open Gmail and click <strong>Compose</strong>.</li>
  <li>Address it to yourself. Write a subject: <em>Biography Document — [Your Name]</em></li>
  <li>In the body, write a proper greeting, then: <em>Please find my biography document attached for your review.</em> Add a sign-off.</li>
  <li>Click the <strong>paperclip icon</strong> at the bottom of the compose window. Navigate to your <em>my_biography.docx</em> file and click <strong>Open</strong>.</li>
  <li>Confirm the file name appears under the body text. Then click <strong>Send</strong>.</li>
  <li>Open your <em>Sent</em> folder and screenshot the sent email showing the attachment.</li>
</ol>

<h3>Before You Start the Mission</h3>
<p>You now know how to write with appropriate professional tone and how to attach files correctly. In your mission, you will compose a full professional email to yourself, attach your biography document, and screenshot it before sending. Every element — greeting, body, attachment, sign-off — must be present and correct.</p>""",
"how to send email with attachment gmail professional email etiquette"),

10: ("""<h3>What You Will Learn</h3>
<ul>
  <li>How to create a structured, multi-paragraph document with headings</li>
  <li>How to use heading styles (H1, H2) to organise long documents</li>
  <li>How to format for readability: spacing, alignment, and paragraph flow</li>
</ul>

<h3>Core Concepts</h3>
<p>A single paragraph of text is easy to write. A multi-page professional report is different — it needs <strong>structure</strong>. Structure is what separates a wall of text from a document that a reader can scan, navigate, and understand in minutes.</p>
<p>Think of document structure like the architecture of a building. The title is the front entrance. Headings are the corridors that lead you to each room. Paragraphs are the rooms themselves — each one containing a single, complete idea. A visitor can navigate the building (document) without getting lost.</p>
<p><strong>Using Heading Styles</strong></p>
<p>Word processors offer <em>heading styles</em> — pre-formatted text options like Heading 1 (H1), Heading 2 (H2), and Normal. Using them properly does two things: it makes your document look professional, and it creates a <em>navigation structure</em> that screen readers and table-of-contents tools can use.</p>
<ul>
  <li><strong>H1 (Heading 1)</strong>: Your document title — use once, at the very top.</li>
  <li><strong>H2 (Heading 2)</strong>: Major section titles (e.g., Introduction, Main Body, Conclusion).</li>
  <li><strong>Normal / Body text</strong>: Your paragraphs. Keep them to 3–5 sentences each.</li>
</ul>
<p><strong>Paragraph Flow</strong></p>
<p>Each paragraph should follow the <strong>PIE structure</strong>: <em>Point</em> (state your main idea), <em>Illustration</em> (give an example or detail), <em>Explanation</em> (connect it back to the point). This keeps writing clear and purposeful.</p>

<h3>Key Terms</h3>
<dl>
  <dt>Heading Style</dt>
  <dd>A pre-formatted text style (H1, H2, H3) used to label sections of a document, making it easy to navigate and visually organised.</dd>
  <dt>Paragraph</dt>
  <dd>A group of sentences developing a single idea, separated from other paragraphs by a line break or spacing.</dd>
  <dt>Alignment</dt>
  <dd>How text is positioned horizontally on a line: Left (ragged right edge), Centred, Right, or Justified (spread evenly to both margins).</dd>
  <dt>Line Spacing</dt>
  <dd>The vertical distance between lines of text. Standard professional documents use 1.15 or 1.5 line spacing for readability.</dd>
</dl>

<h3>Step-by-Step Example</h3>
<p>Creating a structured travel report:</p>
<ol>
  <li>Open your word processor and start a new document.</li>
  <li>Type your title: <em>A Visit to [Your Chosen Place]</em>. Select it and apply <strong>Heading 1</strong> from the styles dropdown.</li>
  <li>Press Enter and type <em>Introduction</em>. Apply <strong>Heading 2</strong>. Write 2–3 sentences introducing your chosen destination.</li>
  <li>Add three more H2 sections: <em>Geography and Climate</em>, <em>Culture and People</em>, <em>Why I Want to Visit</em>. Write one paragraph under each.</li>
  <li>Add a final H2: <em>Conclusion</em>. Write 2 sentences wrapping up your report.</li>
  <li>Press <strong>Ctrl + S</strong>, save as <strong>Travel_Report.docx</strong> in your Bootcamp Projects folder.</li>
</ol>

<h3>Before You Start the Mission</h3>
<p>You now know how to build a structured, multi-section document using heading styles and clear paragraph formatting. In your mission, you will write a 300-word travel report about a place you would like to visit — using a title, three body sections with headings, and a conclusion. Let your writing breathe: short paragraphs, clear headings, proper spacing.</p>""",
"how to use headings and structure a report in microsoft word google docs"),

11: ("""<h3>What You Will Learn</h3>
<ul>
  <li>How to research a topic using multiple websites and compare the information</li>
  <li>How to synthesise information — combining key points from different sources into your own words</li>
  <li>How to cite your sources so others can verify your work</li>
</ul>

<h3>Core Concepts</h3>
<p>Anyone can copy and paste text from a website. <strong>Research</strong> is something more: it is the skill of visiting multiple sources, comparing what they say, identifying what is consistent and credible, and then expressing those ideas in your own words. This skill is valued in every profession.</p>
<p>Think of research like asking the same question to three different market traders. If all three give you the same price, you can be confident it is accurate. If one gives you a wildly different number, you dig deeper. Synthesising research works the same way.</p>
<p><strong>The Research Process</strong></p>
<ol>
  <li><strong>Choose a focused topic</strong> — not too broad (<em>Africa</em>) and not too narrow (<em>the exact number of trees on one street in Nairobi</em>). Something like <em>the benefits of solar energy in rural West Africa</em> is a good scope.</li>
  <li><strong>Visit at least three sources</strong> — search for the topic, open 3 different pages from 3 different sites.</li>
  <li><strong>Read and take notes</strong> — do not copy sentences. Write the key point in your own words. Note the URL of each source.</li>
  <li><strong>Synthesise</strong> — write a summary that weaves together the most important points. Your voice leads; the sources support.</li>
  <li><strong>Cite your sources</strong> — list the URLs at the bottom so readers can verify your information.</li>
</ol>
<p>Writing in your own words is essential — it shows understanding, not just copy-pasting. It also protects you from plagiarism, which is presenting someone else work as your own.</p>

<h3>Key Terms</h3>
<dl>
  <dt>Research</dt>
  <dd>The systematic process of gathering information from multiple reliable sources to answer a question or explore a topic.</dd>
  <dt>Synthesis</dt>
  <dd>Combining information from multiple sources into a coherent, original piece of writing — in your own words.</dd>
  <dt>Citation</dt>
  <dd>A reference to a source you used, typically including the title, author (if known), URL, and date accessed.</dd>
  <dt>Plagiarism</dt>
  <dd>Presenting someone else words or ideas as your own, without giving them credit. It is considered dishonest in academic and professional settings.</dd>
</dl>

<h3>Step-by-Step Example</h3>
<p>Researching and synthesising a topic:</p>
<ol>
  <li>Choose a topic you are curious about — for example, <em>the history of Afrobeats music</em>.</li>
  <li>Search for it on Google. Open the first credible result and read it. Write one sentence in your own words summarising the most important point. Copy the URL.</li>
  <li>Go back and open a second result from a different website. Do the same.</li>
  <li>Open a third source. Take notes again.</li>
  <li>Open a new document. Write a 200-word summary using your three sets of notes. At the bottom, add a <em>Sources</em> heading and list the three URLs.</li>
  <li>Save as <strong>research_summary.docx</strong> in Bootcamp Projects.</li>
</ol>

<h3>Before You Start the Mission</h3>
<p>You now understand how to research, synthesise, and cite. In your mission, you will pick a topic that interests you, visit three websites, take notes from each, and write a 200-word synthesis document with sources listed at the bottom. The quality of your synthesis — not just the length — is what matters here.</p>""",
"how to research and synthesise information from multiple sources for beginners"),

12: ("""<h3>What You Will Learn</h3>
<ul>
  <li>How to safely download files from the internet and verify they are safe</li>
  <li>How to organise downloaded media files with clear, descriptive names</li>
  <li>How to identify trusted download sources and avoid malware</li>
</ul>

<h3>Core Concepts</h3>
<p>Downloading files is one of the most common things people do online — and one of the most common ways that devices get infected with <em>malware</em> (harmful software). Learning to download safely and organise what you download is a critical digital skill.</p>
<p>Think of downloading like buying goods from a roadside vendor. Some vendors are trustworthy — their products are genuine and safe. Others sell counterfeits or spoiled goods. Your job is to tell the difference before you buy.</p>
<p><strong>Safe Downloading Habits</strong></p>
<ul>
  <li><strong>Only download from trusted sources</strong> — official websites, well-known platforms (Unsplash, Pixabay for images; official app stores for software). If a site you do not recognise is offering a free download that seems too good to be true, it probably is.</li>
  <li><strong>Check the file extension</strong> — an image should end in .jpg, .png, or .webp. A document in .docx or .pdf. If a supposed image file ends in .exe, do not open it — .exe means it is a program, which could be malware.</li>
  <li><strong>Check your browser warnings</strong> — if Chrome or Firefox says "This file may harm your computer," take that seriously.</li>
  <li><strong>Go to Downloads first</strong> — all downloaded files land in your Downloads folder by default. Organise from there.</li>
</ul>
<p><strong>Naming and Organising Media Files</strong></p>
<p>Once downloaded, rename every file descriptively before moving it. A file named <em>IMG_20240312_093412.jpg</em> tells you nothing. A file named <em>sunset_beach_accra.jpg</em> tells you exactly what it is.</p>

<h3>Key Terms</h3>
<dl>
  <dt>Download</dt>
  <dd>The process of transferring a file from the internet (a remote server) to your own computer.</dd>
  <dt>Malware</dt>
  <dd>Software designed to harm your computer or steal your data — includes viruses, spyware, and ransomware. Often disguised as legitimate files.</dd>
  <dt>File Extension</dt>
  <dd>The letters after the dot in a filename (.jpg, .docx, .exe) that identify the file type and the program needed to open it.</dd>
  <dt>Trusted Source</dt>
  <dd>A website or platform with a strong reputation for providing safe, legitimate files — such as official software websites or licensed media platforms.</dd>
</dl>

<h3>Step-by-Step Example</h3>
<p>Downloading and organising free images:</p>
<ol>
  <li>Open your browser and go to <strong>unsplash.com</strong> (free, licensed photos).</li>
  <li>Search for a theme you like — for example, <em>Lagos street market</em>. Click a photo you like.</li>
  <li>Click the <strong>Download free</strong> button. The file goes to your Downloads folder.</li>
  <li>Open File Explorer and navigate to Downloads. Find the image (it will have a generic name).</li>
  <li>Right-click it, choose <strong>Rename</strong>, and give it a clear name like <em>lagos_market_afternoon.jpg</em>.</li>
  <li>Move it to <em>MY DIGITAL LIFE → Photos → Downloaded_Images</em>. Repeat for two more images.</li>
</ol>

<h3>Before You Start the Mission</h3>
<p>You now know how to identify safe download sources, check file types, and organise what you download with clear names. In your mission, you will download three images from a trusted source, rename each descriptively, and move them into a properly organised folder. Clean downloads, clean folders — that is the standard from here on.</p>""",
"how to safely download files from the internet for beginners avoid malware"),

13: ("""<h3>What You Will Learn</h3>
<ul>
  <li>The most common digital threats: viruses, phishing, and social engineering</li>
  <li>How to identify a suspicious email or message before it is too late</li>
  <li>Simple habits that protect your accounts and your data</li>
</ul>

<h3>Core Concepts</h3>
<p>Cybersecurity is not just for IT professionals. Every person who uses a phone, computer, or internet connection is a potential target for digital criminals. The good news is that most attacks succeed not because they are technically sophisticated — they succeed because people are not watching for them. Learning to recognise threats is your best defence.</p>
<p><strong>Common Digital Threats</strong></p>
<ul>
  <li><strong>Phishing</strong>: A fake message (email, SMS, or social media) designed to trick you into revealing your password, banking details, or personal information. The message often pretends to be from a bank, government agency, or popular service.</li>
  <li><strong>Malware</strong>: Harmful software secretly installed on your device when you click a suspicious link or download an infected file. It can steal data, lock your files (ransomware), or spy on you.</li>
  <li><strong>Social Engineering</strong>: Manipulating people psychologically to give up information or access. A common example is someone calling and pretending to be your bank, creating urgency: <em>Your account will be suspended in 10 minutes unless you confirm your PIN now.</em></li>
</ul>
<p><strong>Red Flags in Suspicious Messages</strong></p>
<ul>
  <li>Urgent or threatening language: <em>Act now or lose your account!</em></li>
  <li>Sender address does not match the company (e.g., support@amazon-security-help.net instead of @amazon.com)</li>
  <li>Spelling errors and poor grammar in official-looking messages</li>
  <li>Requests for passwords, PINs, or bank details via email or SMS</li>
  <li>Links that do not match the described destination (hover over the link to see the real URL)</li>
</ul>

<h3>Key Terms</h3>
<dl>
  <dt>Phishing</dt>
  <dd>A fraudulent message pretending to be from a trusted source, designed to steal your login credentials, money, or personal data.</dd>
  <dt>Social Engineering</dt>
  <dd>Psychological manipulation used to trick people into revealing confidential information or taking harmful actions.</dd>
  <dt>Malware</dt>
  <dd>Any software intentionally designed to harm a device, steal data, or give an attacker unauthorised access.</dd>
  <dt>Two-Factor Authentication (2FA)</dt>
  <dd>A security method that requires two forms of verification to log in — usually a password plus a code sent to your phone — making it much harder for attackers to access your account.</dd>
</dl>

<h3>Step-by-Step Example</h3>
<p>Analysing a suspicious email:</p>
<ol>
  <li>Imagine you receive an email: <em>Subject: URGENT — Your GTBank account is locked. Click here to verify now.</em></li>
  <li>Check the sender address: it reads <em>security@gtbank-alerts-ng.com</em>. The real domain is <em>gtbank.com</em> — this is a fake domain. Red flag number one.</li>
  <li>Notice the subject line uses the word URGENT in capital letters — a classic pressure tactic. Red flag two.</li>
  <li>Hover over the link without clicking. The actual URL shown is a random string — not gtbank.com. Red flag three.</li>
  <li>Do not click. Mark as spam. If you are worried about your real account, open a new tab and navigate to the bank website directly by typing the URL yourself.</li>
</ol>

<h3>Before You Start the Mission</h3>
<p>You now understand how phishing and social engineering work, and the red flags that reveal them. In your mission, you will create a security audit document — listing five specific red flags from a real or imagined suspicious email, and writing what you would do if you received it. Awareness is your first and strongest line of defence.</p>""",
"how to identify phishing emails and avoid online scams beginner cybersecurity"),

14: ("""<h3>What You Will Learn</h3>
<ul>
  <li>How to set up and join a video conference using Google Meet or Zoom</li>
  <li>How to manage your audio, video, and screen sharing during a meeting</li>
  <li>Professional etiquette for online meetings</li>
</ul>

<h3>Core Concepts</h3>
<p>Video conferencing has transformed how work happens. Remote teams meet, students attend classes, and freelancers present to international clients — all through platforms like <strong>Google Meet</strong>, <strong>Zoom</strong>, and <strong>Microsoft Teams</strong>. Knowing how to run a professional video meeting is now as essential as knowing how to use email.</p>
<p>Think of a video meeting like a face-to-face meeting conducted through a window. You can see each other, share documents, and collaborate in real time — but the technology requires some setup and etiquette to work smoothly.</p>
<p><strong>Key Meeting Controls</strong></p>
<ul>
  <li><strong>Microphone (Mute/Unmute)</strong>: Mute yourself when you are not speaking. Background noise — traffic, a generator, market sounds — is distracting for everyone. Unmute when it is your turn to speak.</li>
  <li><strong>Camera (Video on/off)</strong>: In professional settings, keeping your camera on shows engagement. Make sure your background is neutral or tidy.</li>
  <li><strong>Screen Share</strong>: Lets you show your screen to all participants — useful for presenting documents, showing work, or demonstrating a process.</li>
  <li><strong>Meeting Link</strong>: Every meeting has a unique link. Share this link to invite others. Only people with the link (or meeting ID) can join.</li>
</ul>
<p><strong>Online Meeting Etiquette</strong></p>
<ul>
  <li>Join a few minutes early.</li>
  <li>Test your microphone and camera before the meeting starts.</li>
  <li>Use the <em>Raise Hand</em> feature to signal you want to speak.</li>
  <li>Look at the camera (not the screen) when talking — it simulates eye contact.</li>
</ul>

<h3>Key Terms</h3>
<dl>
  <dt>Video Conference</dt>
  <dd>A real-time meeting conducted over the internet using audio and video, allowing people in different locations to communicate face-to-face.</dd>
  <dt>Screen Sharing</dt>
  <dd>A feature that broadcasts your computer screen to other meeting participants so everyone can see what you are working on.</dd>
  <dt>Meeting Link</dt>
  <dd>A unique URL that grants access to a specific video conference room. Shared with invitees to allow them to join.</dd>
  <dt>Bandwidth</dt>
  <dd>The amount of data your internet connection can transfer per second. Low bandwidth causes pixelated video or choppy audio in video calls.</dd>
</dl>

<h3>Step-by-Step Example</h3>
<p>Starting your first Google Meet session:</p>
<ol>
  <li>Open your browser and go to <strong>meet.google.com</strong>. Sign in with your Gmail account.</li>
  <li>Click <strong>New meeting</strong> then <strong>Start an instant meeting</strong>. Your meeting room opens.</li>
  <li>Allow the browser to access your camera and microphone when prompted.</li>
  <li>Click the <strong>copy link</strong> icon to copy your meeting URL. This is what you would share with others.</li>
  <li>Practice: click the <strong>microphone icon</strong> to mute, then unmute. Click the <strong>camera icon</strong> to turn video off, then on.</li>
  <li>Click the <strong>screen share icon</strong> (a square with an arrow), choose <strong>Your entire screen</strong>, and click Share. You should see your desktop being broadcast.</li>
  <li>Take a screenshot showing your meeting window with screen sharing active.</li>
</ol>

<h3>Before You Start the Mission</h3>
<p>You now know how to start a meeting, share the link, control your audio and video, and share your screen. In your mission, you will start a real Google Meet or Zoom session, test all these controls, and screenshot the active meeting with screen sharing visible — proving you can host a professional video conference independently.</p>""",
"how to use google meet for beginners screen sharing and meeting controls"),

15: ("""<h3>What You Will Learn</h3>
<ul>
  <li>What cloud storage is and why it is more powerful than saving files only on your device</li>
  <li>How to upload files to Google Drive and organise them in the cloud</li>
  <li>How to share files and folders with others using a shareable link</li>
</ul>

<h3>Core Concepts</h3>
<p>Imagine your important documents are stored only on your laptop. The laptop is stolen, or it falls in water. Your files — your biography, your reports, your photos — are gone forever. <strong>Cloud storage</strong> prevents this nightmare. It saves your files on remote servers maintained by a company (like Google), so you can access them from any device, anywhere, as long as you have internet.</p>
<p>Think of it like keeping your money in a bank instead of under your mattress. The bank holds it securely, you can access it from any branch, and you are protected if something happens at home.</p>
<p><strong>Google Drive Basics</strong></p>
<p>Google Drive (drive.google.com) gives every Gmail user 15GB of free storage. Your Drive shows all your files and folders in a clean interface. You can create folders, upload files from your computer, and open documents directly in the browser.</p>
<p><strong>Sharing Files</strong></p>
<p>The real power of cloud storage is collaboration. Instead of emailing large files (which may exceed attachment limits), you share a <em>link</em>. The recipient clicks the link and views or downloads the file directly from the cloud.</p>
<p>When you share, you control the <em>permission level</em>: <em>Viewer</em> (can only read), <em>Commenter</em> (can add comments), or <em>Editor</em> (can make changes). For public sharing, choose <em>Anyone with the link can view</em>.</p>

<h3>Key Terms</h3>
<dl>
  <dt>Cloud Storage</dt>
  <dd>Saving files on remote internet servers (not just your local device), so they can be accessed from any connected device.</dd>
  <dt>Google Drive</dt>
  <dd>Google free cloud storage service offering 15GB per account, accessible at drive.google.com.</dd>
  <dt>Shareable Link</dt>
  <dd>A URL generated for a specific file or folder that allows others to access it without needing their own copy.</dd>
  <dt>Permission Level</dt>
  <dd>The level of access a shared user has: Viewer (read-only), Commenter (can annotate), or Editor (can modify the file).</dd>
</dl>

<h3>Step-by-Step Example</h3>
<p>Uploading and sharing your Travel Report:</p>
<ol>
  <li>Go to <strong>drive.google.com</strong> and sign in with your Gmail account.</li>
  <li>Click the <strong>+ New</strong> button (top left) and choose <strong>File upload</strong>.</li>
  <li>Navigate to your <em>Travel_Report.docx</em> in Bootcamp Projects and click <strong>Open</strong>. The file uploads in seconds.</li>
  <li>Right-click the uploaded file in Drive. Choose <strong>Share</strong>.</li>
  <li>In the share dialog, click <strong>Change to anyone with the link</strong>. Set the permission to <em>Viewer</em>.</li>
  <li>Click <strong>Copy link</strong>. Paste this link into a document called <em>cloud_links.txt</em> in your Documents folder.</li>
  <li>Take a screenshot showing the share dialog with the link generated.</li>
</ol>

<h3>Before You Start the Mission</h3>
<p>You now understand cloud storage and can upload, organise, and share files on Google Drive. In your mission, you will upload your Travel Report, generate a shareable link, and save that link locally — practising the workflow that professionals and students use every day to collaborate across distances.</p>""",
"how to use google drive for beginners upload share files cloud storage"),

16: ("""<h3>What You Will Learn</h3>
<ul>
  <li>What makes a password strong and why weak passwords are dangerous</li>
  <li>How Two-Factor Authentication (2FA) works and why you should enable it</li>
  <li>How to audit and improve your account security today</li>
</ul>

<h3>Core Concepts</h3>
<p>Your password is the lock on the front door of your digital life. If the lock is weak — a simple word, a birthday, or <em>password123</em> — a criminal can guess it. Once inside, they can access your email, your bank, your photos, and your contacts. Strong passwords and 2FA are your strongest and most practical defences.</p>
<p><strong>What Makes a Password Strong?</strong></p>
<p>A strong password is one that is very hard to guess — even by a computer program trying millions of combinations per second. The characteristics:</p>
<ul>
  <li><strong>Length</strong>: At least 12 characters. Length is more powerful than complexity alone.</li>
  <li><strong>Mix of character types</strong>: Uppercase, lowercase, numbers, and symbols (e.g., !, @, #, $).</li>
  <li><strong>Not a real word or name</strong>: Dictionary words are the first things attackers try.</li>
  <li><strong>Unique</strong>: Never reuse the same password across multiple accounts. If one is breached, all are at risk.</li>
</ul>
<p>Compare: <em>Password123!</em> looks complex but is extremely common and would be cracked quickly. <em>T3dh#9!mZ$q2</em> is random, long, and mixed — far harder to crack.</p>
<p><strong>Two-Factor Authentication (2FA)</strong></p>
<p>Even a strong password can be stolen (phishing, data breaches). 2FA adds a second layer: after entering your password, you must also enter a short code sent to your phone or generated by an app. Even if a criminal has your password, they cannot log in without your phone. Enable 2FA on every account that offers it — especially email and banking.</p>

<h3>Key Terms</h3>
<dl>
  <dt>Password Strength</dt>
  <dd>A measure of how difficult a password is to guess or crack, based on its length, complexity, and randomness.</dd>
  <dt>Two-Factor Authentication (2FA)</dt>
  <dd>A security feature requiring two forms of verification to access an account — typically your password plus a one-time code sent to your phone.</dd>
  <dt>Data Breach</dt>
  <dd>An incident where user data (including passwords) is stolen from a company database and may be sold or published online.</dd>
  <dt>Password Manager</dt>
  <dd>Software that securely stores and generates strong, unique passwords for all your accounts, so you only need to remember one master password.</dd>
</dl>

<h3>Step-by-Step Example</h3>
<p>Reviewing and enabling 2FA on your Google account:</p>
<ol>
  <li>Go to <strong>myaccount.google.com</strong> and sign in.</li>
  <li>Click <strong>Security</strong> in the left menu.</li>
  <li>Scroll to <em>How you sign in to Google</em>. Find <strong>2-Step Verification</strong>.</li>
  <li>If it is off, click it and follow the steps. Google will ask for your phone number and send a test code.</li>
  <li>Enter the code to confirm. 2FA is now active.</li>
  <li>Screenshot the Security page showing <em>2-Step Verification: On</em>.</li>
</ol>

<h3>Before You Start the Mission</h3>
<p>You now understand the difference between weak and strong passwords, and why 2FA is your most important account protection. In your mission, you will compare two specific passwords and explain why one is stronger, then navigate to your Google security settings and screenshot evidence that 2FA is enabled on your account.</p>""",
"how to create strong passwords and enable two factor authentication google account"),

17: ("""<h3>What You Will Learn</h3>
<ul>
  <li>What artificial intelligence (AI) is and how AI tools like ChatGPT and Claude work</li>
  <li>What AI tools can and cannot do reliably</li>
  <li>How to use an AI tool to generate useful, clear text output</li>
</ul>

<h3>Core Concepts</h3>
<p>Artificial intelligence tools have gone from science fiction to everyday reality. ChatGPT, Claude, Gemini, and similar tools are now used by students, professionals, writers, and entrepreneurs worldwide. Understanding what they are — and what their limits are — puts you ahead of the majority of users.</p>
<p><strong>What Is an AI Language Model?</strong></p>
<p>An AI language model is software trained on enormous amounts of text from the internet, books, and other sources. It learns patterns in language — how words, sentences, and ideas connect — and uses those patterns to generate new text in response to your prompts. It does not think or understand in the way humans do, but it can produce impressively human-like text.</p>
<p>Think of it like a <strong>very well-read assistant</strong> who has read millions of books and articles but has never lived in the world. They can summarise, explain, draft, and suggest — but they may confidently state things that are wrong (called <em>hallucinations</em>), and they do not have real-time information unless given a tool to access it.</p>
<p><strong>What AI Tools Are Good At</strong></p>
<ul>
  <li>Explaining concepts in simple language</li>
  <li>Drafting emails, reports, and summaries</li>
  <li>Brainstorming ideas and giving feedback on your writing</li>
  <li>Translating or rephrasing text</li>
</ul>
<p><strong>What AI Tools Are NOT Reliable For</strong></p>
<ul>
  <li>Up-to-date facts (their training data has a cutoff date)</li>
  <li>Sensitive personal advice (medical, legal, financial)</li>
  <li>Perfect accuracy — always verify important facts from a primary source</li>
</ul>

<h3>Key Terms</h3>
<dl>
  <dt>Artificial Intelligence (AI)</dt>
  <dd>Computer systems designed to perform tasks that normally require human intelligence, such as understanding language, recognising images, or solving problems.</dd>
  <dt>Language Model</dt>
  <dd>A type of AI trained on large amounts of text data to understand and generate human language.</dd>
  <dt>Prompt</dt>
  <dd>The question, instruction, or input you give an AI tool to get a response.</dd>
  <dt>Hallucination</dt>
  <dd>When an AI generates text that sounds confident and plausible but is factually incorrect or entirely made up.</dd>
</dl>

<h3>Step-by-Step Example</h3>
<p>Using an AI tool for the first time:</p>
<ol>
  <li>Open your browser and go to <strong>claude.ai</strong> or <strong>chatgpt.com</strong>. Create a free account if needed.</li>
  <li>In the message box, type: <em>Explain how the internet works to a complete beginner in under 150 words.</em></li>
  <li>Press Enter (or click the send button). Read the response carefully.</li>
  <li>Ask yourself: Is the explanation clear? Does it match what you learned on Day 6?</li>
  <li>Open a new document called <em>ai_experiment.docx</em> and paste the AI response.</li>
  <li>Below it, write 2–3 sentences: Was it accurate? Was it easy to understand? What would you improve?</li>
  <li>Save the document in Bootcamp Projects and take a screenshot of the AI conversation.</li>
</ol>

<h3>Before You Start the Mission</h3>
<p>You now understand what an AI language model is, what it is good at, and where it falls short. In your mission, you will ask an AI tool a specific question, evaluate the quality of the answer honestly, and record both the output and your reflection. This critical approach — using AI as a tool while remaining the judge — is the mark of a skilled digital worker.</p>""",
"introduction to AI tools for beginners chatgpt claude how to use AI"),

18: ("""<h3>What You Will Learn</h3>
<ul>
  <li>What a structured prompt is and why it produces better AI responses than simple questions</li>
  <li>How to use role-based prompting to get professional-quality output</li>
  <li>How to iterate — refining your prompt when the first response is not what you need</li>
</ul>

<h3>Core Concepts</h3>
<p>Yesterday you used an AI tool for the first time. Today you go deeper: you learn to <em>communicate with AI intentionally</em>. The quality of what an AI produces is directly tied to the quality of your prompt. A vague question gets a vague answer. A precise, structured prompt gets professional output.</p>
<p>Think of it like ordering food at a restaurant. If you say <em>bring me something to eat</em>, you might get anything. If you say <em>I would like jollof rice with grilled chicken, extra spicy, no onions</em>, you get exactly what you want. Prompting is the same: be specific about the role, the format, the length, and the context.</p>
<p><strong>The Role-Based Prompt Structure</strong></p>
<p>One of the most effective prompt patterns gives the AI a <em>role</em> before asking the question:</p>
<blockquote>Act as a [role]. [Context]. Write a [output type] that [specific requirement].</blockquote>
<p>For example:</p>
<blockquote>Act as a career coach. I just completed a 20-day digital skills bootcamp. Write a professional 3-sentence CV summary highlighting my skills in file management, email communication, cloud tools, and AI tools.</blockquote>
<p>Why does this work? Giving the AI a role activates patterns from relevant expert writing in its training data. The more context you give, the more tailored and useful the output.</p>
<p><strong>Iterating on Prompts</strong></p>
<p>If the first response is not quite right, do not start over — refine it. Add: <em>Make it more formal.</em> Or: <em>Shorten it to two sentences.</em> Or: <em>Use language suitable for a school leaver in Nigeria.</em> Each refinement brings the output closer to what you need.</p>

<h3>Key Terms</h3>
<dl>
  <dt>Prompt Engineering</dt>
  <dd>The skill of crafting AI inputs (prompts) carefully to get high-quality, relevant, and accurate outputs.</dd>
  <dt>Role-Based Prompt</dt>
  <dd>A prompt that begins by assigning the AI a specific role (e.g., career coach, editor, teacher), which shapes the style and expertise of its response.</dd>
  <dt>Iteration</dt>
  <dd>The process of refining a prompt (or any work) through successive improvements until the output meets your needs.</dd>
  <dt>Context Window</dt>
  <dd>The amount of text an AI can hold in its memory during a single conversation. Longer conversations may cause the AI to forget earlier details.</dd>
</dl>

<h3>Step-by-Step Example</h3>
<p>Using a role-based prompt to write a CV summary:</p>
<ol>
  <li>Open your AI tool (claude.ai or chatgpt.com).</li>
  <li>Type this exact prompt: <em>Act as a career coach. I just completed a 20-day digital skills bootcamp covering file management, email, cloud tools, video conferencing, cybersecurity, and AI. Write a professional 3-sentence CV summary suitable for entry-level office roles.</em></li>
  <li>Read the response. Is it professional in tone? Does it mention the right skills?</li>
  <li>If it is too long, follow up: <em>Make it exactly three sentences and more concise.</em></li>
  <li>Copy the final version into your <em>ai_experiment.docx</em> document.</li>
  <li>Add one sentence reflecting on how the role-based prompt changed the quality compared to your Day 17 simple question.</li>
</ol>

<h3>Before You Start the Mission</h3>
<p>You now understand how structured, role-based prompts unlock better AI output — and how to iterate until you get what you need. In your mission, you will use the exact prompt structure from today lesson to generate a professional CV summary, then reflect on how the structured prompt changed the quality of the output. This skill will serve you for every AI tool you use going forward.</p>""",
"prompt engineering for beginners how to write better AI prompts ChatGPT Claude"),

19: ("""<h3>What You Will Learn</h3>
<ul>
  <li>What a digital portfolio is and why it matters for your career</li>
  <li>How to curate and organise your best work into a presentable collection</li>
  <li>How to create and share a professional portfolio folder on Google Drive</li>
</ul>

<h3>Core Concepts</h3>
<p>Over the past 18 days, you have created real work: a biography, a travel report, research documents, security audits, AI experiments, and more. Today you stop and look at everything you have built. You will organise it into a <strong>digital portfolio</strong> — a curated collection of your best work that demonstrates your skills to employers, educators, or collaborators.</p>
<p>Think of a portfolio like a tailor shop window display. The tailor does not put every piece of fabric and every sample in the window — they choose the items that best show their skill. Your portfolio works the same way: quality and organisation over quantity.</p>
<p><strong>Why a Digital Portfolio Matters</strong></p>
<p>In today job market, saying "I can use a computer" is not enough. Showing a well-organised Google Drive folder with properly named, professional documents proves it. A digital portfolio:</p>
<ul>
  <li>Gives concrete evidence of your skills</li>
  <li>Shows you are organised and professional</li>
  <li>Can be shared instantly via a single link</li>
  <li>Works as the foundation for a CV or job application</li>
</ul>
<p><strong>Curation Principles</strong></p>
<p>Choose documents that demonstrate a range of skills. For each file you include, ask: Does this show something I learned? Is it clearly named? Is it complete and well-formatted? If yes, it belongs in the portfolio. Rename files that have vague names before uploading.</p>

<h3>Key Terms</h3>
<dl>
  <dt>Digital Portfolio</dt>
  <dd>A curated collection of digital files and projects that demonstrates a person skills, knowledge, and work quality.</dd>
  <dt>Curation</dt>
  <dd>The process of carefully selecting and organising items (documents, photos, work samples) to present the best representation of your abilities.</dd>
  <dt>Cloud Folder</dt>
  <dd>A folder stored on a cloud platform (like Google Drive) that can be shared with others via a link, allowing them to view or download its contents.</dd>
  <dt>Shareable Folder Link</dt>
  <dd>A URL that gives anyone who has it access to view (or edit) the entire contents of a shared cloud folder.</dd>
</dl>

<h3>Step-by-Step Example</h3>
<p>Building your Digital Portfolio on Google Drive:</p>
<ol>
  <li>Go to <strong>drive.google.com</strong> and sign in.</li>
  <li>Click <strong>+ New</strong> and create a folder named <em>[YourName]_Digital_Portfolio</em>.</li>
  <li>Upload these four key documents: <strong>my_biography.docx</strong>, <strong>Travel_Report.docx</strong>, <strong>ai_experiment.docx</strong>, <strong>security_audit.txt</strong>.</li>
  <li>After uploading, right-click the portfolio folder. Choose <strong>Share</strong>. Set to <em>Anyone with the link can view</em>. Copy the link.</li>
  <li>Open your local <em>cloud_links.txt</em> file and add a new line: <em>Portfolio Folder: [paste the link]</em>. Save it.</li>
  <li>Screenshot the portfolio folder on Google Drive showing all four files.</li>
</ol>

<h3>Before You Start the Mission</h3>
<p>You now understand what a digital portfolio is, how to curate it, and how to share it professionally. In your mission, you will create your portfolio folder on Google Drive, upload your four key documents, set the sharing permissions, and save the link. You are one day away from graduation — make sure every file is named clearly and looks professional.</p>""",
"how to create a digital portfolio on google drive for beginners career"),

20: ("""<h3>What You Will Learn</h3>
<ul>
  <li>How to review, organise, and complete your full 20-day body of work</li>
  <li>How to structure a final portfolio that demonstrates your complete digital skill set</li>
  <li>How to write a reflective summary of your learning journey</li>
</ul>

<h3>Core Concepts</h3>
<p>You have reached Day 20 — the final day of the Dako Studios Digital Skills Bootcamp. Over these twenty days you have travelled from not knowing the difference between hardware and software to managing cloud storage, defending against cyber threats, and directing AI tools with professional prompts. That is a remarkable journey, and today you honour it.</p>
<p><strong>What the Capstone Is About</strong></p>
<p>A <em>capstone project</em> is a final, comprehensive task that brings together everything you have learned. It is not a new skill — it is proof of all the old ones, organised and presented with intention. Your capstone today is to build a <strong>Graduation Hub</strong> on Google Drive: a master portfolio that is clean, structured, shareable, and ready to show the world.</p>
<p><strong>Organisation as a Skill</strong></p>
<p>By now you have many digital files. A disorganised pile of documents, even if each one is excellent, fails to communicate your capability. The way you organise your Graduation Hub is itself a demonstration of skill. Recruiters, educators, and clients will see your folder structure before they read a single file.</p>
<p><strong>Reflection as Learning</strong></p>
<p>The most effective learners do not just complete tasks — they pause and think about what changed. Your 100-word reflection is not a formality. It forces you to articulate growth: What was hardest? What was most useful? What will you do differently because of what you learned? Writing this cements your learning in a way that completing tasks alone cannot.</p>

<h3>Key Terms</h3>
<dl>
  <dt>Capstone Project</dt>
  <dd>A culminating task that integrates and demonstrates the full range of skills learned throughout a course or programme.</dd>
  <dt>Digital Citizen</dt>
  <dd>A person who uses digital tools effectively, responsibly, and safely as a regular part of their professional and personal life.</dd>
  <dt>Portfolio Review</dt>
  <dd>The process of examining your collected work, identifying the strongest pieces, and ensuring they are well-organised and professionally presented.</dd>
  <dt>Reflective Writing</dt>
  <dd>Writing in which you examine your own experiences, decisions, and learning — analysing what happened, what you felt, and what you would do differently.</dd>
</dl>

<h3>Step-by-Step Example</h3>
<p>Building your Graduation Hub:</p>
<ol>
  <li>Go to <strong>drive.google.com</strong> and create a master folder: <em>[YourName]_Dako_Graduation_Hub</em>.</li>
  <li>Inside it, create two sub-folders: <strong>01_Foundations</strong> and <strong>02_Projects</strong>.</li>
  <li>Move your foundational documents (hardware_check.txt, my_biography.docx, search_results.txt) into <em>01_Foundations</em>.</li>
  <li>Move your project documents (Travel_Report.docx, research_summary.docx, ai_experiment.docx, security_audit.txt) into <em>02_Projects</em>.</li>
  <li>In a new document, write your 100-word reflection: What was your biggest learning? Which skill will you use first in your daily or professional life?</li>
  <li>Save the reflection as <em>Graduation_Reflection.docx</em> and upload it to the Graduation Hub.</li>
  <li>Set the master folder sharing to <em>Anyone with the link can view</em>. Copy and save the link.</li>
  <li>Screenshot your final organised hub showing both sub-folders and all documents.</li>
</ol>

<h3>Before You Start the Mission</h3>
<p>This is your final mission. Everything you have built over 20 days comes together here. Follow the steps carefully, write your reflection honestly, and submit with the pride of someone who has genuinely learned. You have earned the <strong>Digital Citizen badge</strong>. Welcome to your digital life.</p>""",
"digital skills portfolio how to organise your work for job applications beginners"),
}

conn = sqlite3.connect(str(DB))
cur = conn.cursor()

for day, (html, video) in LESSONS.items():
    cur.execute(
        "UPDATE curriculum SET lesson_html=?, video_url=?, lesson_status='draft' WHERE day=?",
        (html.strip(), video, day)
    )
    print(f"Day {day:2d} saved ({len(html.split())} words)")

conn.commit()
conn.close()
print("\nAll lessons inserted.")
