# EMERGENCY RESEARCH SPRINT: Project Konflux Analysis
**Intelligence Analyst: Maya**
**Date: 2026-04-09**
**Duration: 30 minutes**
**Status: COMPLETE**

## Executive Summary

Bob's revelation about Project Konflux is **HIGHLY SIGNIFICANT** and fundamentally changes our technical feasibility assessment. Konflux is Red Hat's production-grade, opensource build system that has replaced traditional systems for many use cases. This validates the technical feasibility of our approach while also revealing we may be able to leverage existing infrastructure rather than build from scratch.

## Key Finding: CRITICAL STRATEGIC SHIFT REQUIRED

**Recommendation: PIVOT from "build replacement" to "fork and extend" strategy**

---

## Question 1: GitHub Repository & Architecture Analysis

### Repository Location
- **Main Repo**: https://github.com/konflux-ci/konflux-ci (Integration and release)
- **Organization**: https://github.com/konflux-ci
- **License**: Apache 2.0 (fully opensource)

### Architecture Components

Konflux is built on a **Kubernetes-native foundation** with these core elements:

1. **Tekton** - Underlying pipeline orchestration engine (NOT a replacement, but foundation)
2. **Enterprise Contract (Conforma)** - Policy framework for governance using Rego/OPA
3. **Konflux Operator** - Kubernetes operator managing platform deployment
4. **Cosign** - Container image signing
5. **SLSA Provenance** - Supply chain attestation (SLSA Level 3 compliance)
6. **Tekton Chains** - Attestation generation
7. **SPIFFE/SPIRE** - Identity framework
8. **Hermeto** - Hermetic build component
9. **Project Quay** - Container registry

### Platform Lifecycle Management

Four primary stages:
1. **Build** – Triggers Tekton pipelines on every PR/merge, produces signed container images
2. **Test** – Runs integration scenarios with Enterprise Contract policies for release gating
3. **Release** – Declarative model using ReleasePlan/ReleasePlanAdmission Kubernetes resources
4. **Security** – Every artifact signed with cosign, attested with SLSA provenance, policy-checked

### Project Maturity
- 5,891 commits on main branch
- 114 stars, 129 forks
- 46 releases (latest: v0.1.8, April 2026)
- Primary language: Go (87.6%)

---

## Question 2: Adoption Timeline - When Did Red Hat Transition?

### Critical Finding: GRADUAL ROLLOUT, NOT COMPLETE REPLACEMENT

**Status as of April 2026:**
- Konflux is **IN PRODUCTION** at Red Hat
- Built **over 2 million software artifacts** for customers in 2025 alone
- Supports multiple architectures: x86_64, PPC64, ARM, and Z
- SLSA Level 3 compliance across all builds

**Timeline Evidence:**
- GitHub activity shows Konflux operational throughout 2024
- Red Hat Konflux bot making automated updates as early as February 2024
- Platform previously known as "Red Hat AppStudio" before rebranding to Konflux
- No specific GA announcement date found, suggesting phased internal rollout

**Fedora Adoption (Proxy for Red Hat Timeline):**
- Fedora currently working to set up Konflux as **parallel build system** for bootc images
- As of early 2025: Konflux used only in **proof-of-concept capacity** for pre-release images
- Fedora 44 (April 2025) final images still produced by **Koji**
- RPM support only recently added to Konflux (containers came first)
- Quote from Fedora discussions: "Its development isn't far enough along that it could replace koji any time soon"

**Key Insight:** This is an **ONGOING TRANSITION**, not a completed one. Red Hat is running Konflux in production for specific use cases while maintaining legacy systems.

---

## Question 3: Relationship to Existing Systems

### CRITICAL: Konflux Does NOT Replace - It COMPLEMENTS and EVENTUALLY Supersedes

**Current State:**

1. **Tekton Relationship:**
   - Konflux is **BUILT ON TOP OF** Tekton, not a replacement
   - Uses "Tekton framework to run pipelines in your Konflux cluster"
   - Extends Tekton with supply chain security, policy enforcement, and release automation

2. **Koji Relationship:**
   - Konflux is the **"aspirational replacement"** for Koji and other build systems
   - Currently uses "interim rpm approach [that] injects builds into koji after a build is complete"
   - Koji still in active use for RPM builds in many contexts
   - Vision: If successful, Konflux could replace "koji, bodhi, compose hosts, signing hosts, autosign hosts, some ci infra, and more"

3. **Jenkins Relationship:**
   - No explicit mentions of Jenkins in Konflux documentation
   - Konflux provides its own CI/CD capabilities via Tekton pipelines
   - Likely replaces Jenkins use cases where applicable

**Strategic Implication for Our Analysis:**
- Previous research on Koji/Tekton/Jenkins is **NOT INVALIDATED**
- These systems remain in active use during transition period
- Understanding legacy systems still critical for:
  - Integration requirements
  - Migration strategies
  - Hybrid workflows during transition
  - Organizations not yet on Konflux

---

## Question 4: Decision Logic Availability in Opensource Code

### MAJOR FINDING: EXTENSIVE DECISION LOGIC AVAILABLE

**Enterprise Contract (Conforma) - The Policy Engine:**

Repository: https://github.com/enterprise-contract/ec-policies

**Decision Logic Categories:**

1. **Release Policy Validation** - Gates releases based on:
   - Attestation validation (known attestation types present)
   - Base image compliance (permitted registries only)
   - Build task verification (Buildah parameters, Dockerfile usage)
   - **CVE scanning with blocking/non-blocking thresholds**
   - Git source verification (branch, commit validation)
   - SBOM validation (CycloneDX and SPDX formats)
   - SLSA compliance checks (levels 1, 2, 3)
   - Task trust validation (pinned, tagged, trusted sources)
   - RPM-specific checks (signatures, repositories, dependencies)

2. **Pipeline Policy Validation** - Checks:
   - Pipeline task definitions and configurations
   - Compliance with Red Hat trusted task guidelines

**How It Works:**

- Policies written in **Rego language** (Open Policy Agent)
- Rules return **pass/fail** results based on specific criteria
- **Blocking rules** prevent release (e.g., critical CVEs)
- **Warning rules** allow release but flag concerns (e.g., non-blocking CVEs)
- Enforces **100% test coverage** requirement for all policies
- Distributed as **OCI artifacts** via Quay.io for version control

**Approval Gate Mechanism:**

- Release Pipeline contains Enterprise Contract Task
- If EC task fails, release is **blocked**
- EnterpriseContractPolicy CR (Custom Resource) codifies build requirements
- Policy evaluation against snapshots returns single result based on highest violation
- All components must pass for policy evaluation to be true

**Release Automation Decision Logic:**

- **Integration Service** automatically creates releases when:
  - Snapshot successfully passes post-merge testing
  - Automated release flags enabled
- Manual user-initiated releases also supported
- Binary approval decision tied to test outcomes

**Configurable Policy Profiles:**
- "minimal" - Basic checks
- "github" - GitHub-specific validation
- "redhat" - Red Hat enterprise requirements
- "slsa3" - SLSA Level 1, 2, 3 compliance

**What's NOT Available:**
- Explicit rollback mechanisms not documented
- Escalation procedures not detailed in architecture docs
- Human approval workflows beyond policy gates not specified

---

## Question 5: Strategic Implications - Fork/Extend vs. Build from Scratch?

### RECOMMENDATION: FORK AND EXTEND STRATEGY

**YES - Opensource status fundamentally changes our approach:**

**Evidence for Fork/Extend Viability:**

1. **Apache 2.0 License** - Permissive, allows commercial use, modification, distribution
2. **Explicitly Designed for Customization:**
   - "You can set up your own instance of Konflux locally"
   - "Opinionated build pipelines and release pipelines, but letting users extend those and create their own"
   - "Customize your project to meet your specific requirements"
3. **Policy Customization:**
   - "Write policy rules in the rego language"
   - "Release engineering teams can vary these policies by product"
   - "Setting a lower bar for prototypes... higher bar for GA releases"
4. **Sample Repositories:** Konflux provides sample repos "you can fork and try to onboard"

**Strategic Advantages:**

✓ **Proven at Scale:** 2 million artifacts in production use
✓ **Enterprise-Grade Security:** SLSA Level 3 compliance built-in
✓ **Multi-Architecture:** Already supports x86_64, PPC64, ARM, Z
✓ **Active Development:** 46 releases, recent v0.1.8 in April 2026
✓ **Community Support:** Opensource community, documentation, reference implementations
✓ **Reduced Development Time:** Build on proven foundation vs. greenfield development
✓ **Lower Risk:** Leverage Red Hat's engineering investment
✓ **Customizable Decision Logic:** Rego policies allow custom approval gates

**What We Would Extend:**

Based on our previous requirements analysis:
- Custom approval workflows for specific organizational needs
- Additional escalation triggers and rollback automation
- Integration with existing enterprise systems
- Specialized policy rules for specific compliance frameworks
- Enhanced observability and reporting
- Custom release orchestration logic

**Business Case Impact:**

- **Development Costs:** Significantly reduced (fork vs. build)
- **Time to Market:** Faster deployment with proven platform
- **Risk Profile:** Lower technical risk, proven in production
- **Maintenance:** Benefit from upstream improvements
- **Talent Acquisition:** Easier to find Tekton/Kubernetes skills than proprietary build system expertise

---

## IMPACT ON TECHNICAL FEASIBILITY ASSESSMENT

### Previous Assessment: NEEDS REVISION

**What Changes:**

1. **Technical Feasibility:** Increases from "possible" to "proven at scale"
2. **Development Approach:** Shifts from "build" to "fork/extend/customize"
3. **Timeline Estimates:** Likely much shorter with proven foundation
4. **Resource Requirements:** Reduced engineering effort
5. **Risk Assessment:** Lower technical risk, higher confidence

**What Stays the Same:**

1. **Market Need:** Still exists - many organizations need this
2. **Competitive Landscape:** Still relevant to understand
3. **Integration Requirements:** Legacy systems (Koji/Tekton/Jenkins) still in use during transition
4. **Business Model:** Likely shifts toward "managed Konflux" or "Konflux extensions" rather than "proprietary build system"

---

## RECOMMENDED NEXT ACTIONS

### Immediate (Next 24 Hours):
1. **Update Business Plan** - Revise from "build" to "fork/extend" model
2. **Financial Reanalysis** - Recalculate development costs with fork strategy
3. **Competitive Positioning** - Analyze market for "Konflux-as-a-Service" or "Enhanced Konflux"
4. **Legal Review** - Verify Apache 2.0 license implications for commercial offering

### Short-Term (Next Week):
1. **Proof of Concept** - Deploy Konflux instance locally, test customization
2. **Policy Analysis** - Deep dive into ec-policies repo, map to our requirements
3. **Gap Analysis** - Identify what extensions/customizations we need
4. **Partner Discussion** - Reach out to Red Hat/Konflux community

### Medium-Term (Next Month):
1. **Architecture Design** - Design our extensions/customizations
2. **Pilot Implementation** - Build custom policies and workflows
3. **Market Validation** - Test "managed Konflux" value proposition with potential customers

---

## SOURCES

### Official Konflux Resources:
- [Why Konflux? :: Konflux Documentation](https://konflux-ci.dev/docs/)
- [Konflux Official Site](http://konflux-ci.dev/)
- [GitHub - konflux-ci/konflux-ci](https://github.com/konflux-ci/konflux-ci)
- [konflux-ci GitHub Organization](https://github.com/konflux-ci)
- [Architecture of Konflux](https://konflux-ci.dev/architecture/)

### Red Hat Production Usage:
- [How we use software provenance at Red Hat | Red Hat Developer](https://developers.redhat.com/articles/2025/05/15/how-we-use-software-provenance-red-hat)
- [Zero CVEs: The symptom of a larger problem](https://www.redhat.com/en/blog/zero-cves-symptom-larger-problem)
- [Ephemeral OpenShift clusters in Konflux CI](https://developers.redhat.com/articles/2024/10/28/ephemeral-openshift-clusters-konflux-ci-using-cluster-service-operator)

### Adoption and Timeline:
- [Changes/Build FCOS on Fedora Konflux - Fedora Project Wiki](https://fedoraproject.org/wiki/Changes/Build_FCOS_on_Fedora_Konflux)
- [Konflux: What is the right time? - Fedora Discussion](https://discussion.fedoraproject.org/t/konflux-what-is-the-right-time/146722)
- [Fedora shares strategy updates | LWN.net](https://lwn.net/Articles/1060190/)

### Technical Architecture:
- [GitHub - konflux-ci/build-definitions](https://github.com/konflux-ci/build-definitions)
- [GitHub - konflux-ci/docs](https://github.com/konflux-ci/docs)
- [GitHub - konflux-ci/e2e-tests](https://github.com/konflux-ci/e2e-tests)
- [GitHub Apps - Red Hat Konflux](https://github.com/apps/red-hat-konflux)

### Policy and Decision Logic:
- [GitHub - enterprise-contract/ec-policies](https://github.com/enterprise-contract/ec-policies)
- [Enterprise Contract Configuration Files](https://github.com/enterprise-contract/config)
- [Conforma Policies Documentation](https://conforma.dev/docs/policy/index.html)
- [Using custom configuration :: Conforma](https://conforma.dev/docs/user-guide/custom-config.html)

---

## CONCLUSION

Bob's information about Project Konflux is **game-changing**. We have discovered that:

1. Red Hat has already built and opensourced exactly the type of system we were planning to create
2. It's proven at massive scale (2M+ artifacts)
3. It's designed to be forked and extended
4. The decision logic we need is available in opensource Rego policies
5. This dramatically improves our technical feasibility while changing our business model

**Strategic Pivot Required:** From "build proprietary build system" to "provide managed/enhanced Konflux platform"

**Confidence Level:** HIGH - This research is based on official Red Hat documentation, opensource repositories, and Fedora community discussions.

**Time Sensitivity:** CRITICAL - This should inform Dr. Chen's decision-making immediately. The business plan needs revision before proceeding with original "build from scratch" approach.

---

**Research Sprint Completed: 30 minutes**
**Analyst: Maya**
**Date: 2026-04-09**
