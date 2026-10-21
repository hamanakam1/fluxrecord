#!/usr/bin/env python3
"""
fluxrecord Ticket Management Script

This script helps manage the fluxrecord tickets.
It can query, filter, and report on tickets in various formats.

Usage:
    python manage_tickets.py [command] [options]

Commands:
    list                    List all tickets or filtered subset
    show <id>              Show details of a specific ticket
    stats                  Show statistics summary
    by-epic               Group tickets by epic
    by-priority           Group tickets by priority
    by-category           Group tickets by category
    by-complexity         Group tickets by complexity
    sprint <n>            Show tickets for sprint n
    export <format>       Export tickets (csv, json, markdown)
    search <term>         Search tickets by keyword
"""

import csv
import json
import sys
from pathlib import Path
from collections import defaultdict
from typing import List, Dict, Any, Optional

# Path to the tickets CSV file
TICKETS_FILE = Path(__file__).parent / "tickets.csv"

# Default values for optional columns
DEFAULT_VALUES = {
    'Status': 'INBOX',
    'Tags': '',
    'DependsOn': '',
    'Blocks': '',
    'Owner': '',
    'SeedKey': '',
    'CreatedAt': '',
    'UpdatedAt': '',
}

# Load all tickets from CSV
def load_tickets() -> List[Dict[str, Any]]:
    """Load tickets from CSV file."""
    tickets = []
    try:
        with open(TICKETS_FILE, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                # Convert numeric fields
                for key in ['EstimateHoursMin', 'EstimateHoursMax']:
                    if key in row and row[key]:
                        try:
                            row[key] = int(row[key])
                        except ValueError:
                            pass
                # Add default values for missing columns
                for key, default in DEFAULT_VALUES.items():
                    if key not in row:
                        row[key] = default
                tickets.append(row)
    except FileNotFoundError:
        print(f"Error: Tickets file not found at {TICKETS_FILE}")
        sys.exit(1)
    return tickets


def get_priority_color(priority: str) -> str:
    """Get ANSI color code for priority."""
    colors = {
        'P0': '\033[91m',  # Red - Critical
        'P1': '\033[93m',  # Yellow - High
        'P2': '\033[92m',  # Green - Medium
        'P3': '\033[94m',  # Blue - Low
    }
    return colors.get(priority, '\033[0m')


def get_complexity_emoji(complexity: str) -> str:
    """Get emoji for complexity."""
    emojis = {
        'Critical': '🔴',
        'High': '🟠',
        'Medium': '🟡',
        'Low': '🟢',
    }
    return emojis.get(complexity, '')


def get_status_emoji(status: str) -> str:
    """Get emoji for status."""
    emojis = {
        'INBOX': '📥',
        'READY': '✅',
        'IN_PROGRESS': '🔄',
        'BLOCKED': '🚫',
        'REVIEW': '👀',
        'ACCEPTED': '✅',
        'DONE': '🏁',
        'CANCELLED': '❌',
        'PENDING': '⏳',
        'IN_PROGRESS': '🔄',
    }
    return emojis.get(status, '❓')


def format_ticket(ticket: Dict[str, Any], verbose: bool = False) -> str:
    """Format a single ticket for display."""
    priority_color = get_priority_color(ticket.get('Priority', ''))
    complexity_emoji = get_complexity_emoji(ticket.get('Complexity', ''))
    status_emoji = get_status_emoji(ticket.get('Status', ''))
    
    lines = []
    # Check if ID already has M- prefix
    ticket_id = ticket['ID']
    if ticket_id.startswith('M-'):
        display_id = ticket_id
    else:
        display_id = f"M-{ticket_id}"
    header = f"{priority_color}{display_id}\033[0m: {ticket['Title']} {get_status_emoji(ticket.get('Status', ''))} {complexity_emoji}"
    lines.append(header)
    lines.append(f"  Epic: {ticket.get('Epic', 'N/A')}")
    lines.append(f"  Priority: {ticket.get('Priority', 'N/A')} | Complexity: {ticket.get('Complexity', 'N/A')} | Status: {ticket.get('Status', 'N/A')}")
    lines.append(f"  Effort: {ticket.get('EstimateHoursMin', '?')}-{ticket.get('EstimateHoursMax', '?')} hours")
    lines.append(f"  Category: {ticket.get('Category', 'N/A')} | File: {ticket.get('File', 'N/A')}")
    if ticket.get('Tags'):
        lines.append(f"  Tags: {ticket.get('Tags', '')}")
    
    if verbose:
        if ticket.get('Description'):
            lines.append(f"\n  Description: {ticket['Description'][:100]}...")
        if ticket.get('AcceptanceCriteria'):
            lines.append(f"  Acceptance: {ticket['AcceptanceCriteria'][:80]}...")
    
    return '\n'.join(lines)


def print_tickets(tickets: List[Dict[str, Any]], verbose: bool = False) -> None:
    """Print a list of tickets."""
    for ticket in tickets:
        print(format_ticket(ticket, verbose))
        print()


def cmd_list(args: List[str]) -> None:
    """List tickets with optional filters."""
    tickets = load_tickets()
    
    # Parse filters
    filters = {}
    i = 0
    while i < len(args):
        if args[i] in ['--priority', '-p']:
            filters['Priority'] = args[i+1]
            i += 2
        elif args[i] in ['--status', '-s']:
            filters['Status'] = args[i+1]
            i += 2
        elif args[i] in ['--epic', '-e']:
            filters['Epic'] = args[i+1]
            i += 2
        elif args[i] in ['--category', '-c']:
            filters['Category'] = args[i+1]
            i += 2
        elif args[i] in ['--complexity', '-x']:
            filters['Complexity'] = args[i+1]
            i += 2
        elif args[i] in ['--verbose', '-v']:
            verbose = True
            i += 1
        else:
            i += 1
    
    # Apply filters
    filtered = tickets
    for key, value in filters.items():
        if key == 'Epic':
            filtered = [t for t in filtered if value.lower() in t.get('Epic', '').lower()]
        else:
            filtered = [t for t in filtered if t.get(key, '').lower() == value.lower()]
    
    print(f"Found {len(filtered)} tickets:\n")
    print_tickets(filtered, verbose)


def cmd_show(args: List[str]) -> None:
    """Show details of a specific ticket."""
    if not args:
        print("Usage: show <ticket-id> (e.g., M-397001)")
        return
    
    ticket_id_input = args[0]
    tickets = load_tickets()
    
    # Try to find by exact match first
    ticket = next((t for t in tickets if t['ID'] == ticket_id_input), None)
    
    # If not found, try extracting number from M-XXXXXX format
    if ticket is None and ticket_id_input.startswith('M-'):
        try:
            num = int(ticket_id_input[2:])
            ticket = next((t for t in tickets if str(t['ID']).endswith(str(num))), None)
        except ValueError:
            pass
    
    # If still not found, try as integer
    if ticket is None:
        try:
            ticket_id = int(ticket_id_input)
            ticket = next((t for t in tickets if str(t['ID']).endswith(str(ticket_id))), None)
        except ValueError:
            pass
    
    if not ticket:
        print(f"Ticket #{ticket_id} not found")
        return
    
    print("=" * 80)
    print(format_ticket(ticket, verbose=True))
    print("-" * 80)
    print(f"\nFull Description:\n{ticket.get('Description', 'N/A')}")
    print(f"\nAcceptance Criteria:\n{ticket.get('AcceptanceCriteria', 'N/A')}")
    if ticket.get('Dependencies'):
        print(f"\nDependencies: {ticket['Dependencies']}")
    print("=" * 80)


def cmd_stats(args: List[str]) -> None:
    """Show statistics summary."""
    tickets = load_tickets()
    
    # Count by priority
    by_priority = defaultdict(int)
    for t in tickets:
        by_priority[t['Priority']] += 1
    
    # Count by status
    by_status = defaultdict(int)
    for t in tickets:
        by_status[t['Status']] += 1
    
    # Count by category
    by_category = defaultdict(int)
    for t in tickets:
        by_category[t['Category']] += 1
    
    # Count by epic
    by_epic = defaultdict(int)
    for t in tickets:
        by_epic[t['Epic']] += 1
    
    # Count by complexity
    by_complexity = defaultdict(int)
    for t in tickets:
        by_complexity[t['Complexity']] += 1
    
    # Calculate total effort
    total_min = sum(t.get('EstimateHoursMin', 0) for t in tickets)
    total_max = sum(t.get('EstimateHoursMax', 0) for t in tickets)
    
    print("=" * 80)
    print("FLUXRECORD TICKET STATISTICS")
    print("=" * 80)
    print(f"\nTotal Tickets: {len(tickets)}")
    print(f"Total Effort Estimate: {total_min}-{total_max} hours")
    
    print(f"\n📊 By Priority:")
    for priority in ['P0', 'P1', 'P2', 'P3']:
        count = by_priority.get(priority, 0)
        pct = (count / len(tickets)) * 100 if tickets else 0
        bar = '█' * int(pct / 2)
        color = {'P0': '\033[91m', 'P1': '\033[93m', 'P2': '\033[92m', 'P3': '\033[94m'}.get(priority, '')
        print(f"  {color}{priority}\033[0m: {count:3d} ({pct:5.1f}%) {bar}")
    
    print(f"\n📊 By Status:")
    for status in ['INBOX', 'READY', 'IN_PROGRESS', 'BLOCKED', 'REVIEW', 'ACCEPTED', 'DONE', 'CANCELLED', 'PENDING']:
        count = by_status.get(status, 0)
        if count > 0:
            pct = (count / len(tickets)) * 100 if tickets else 0
            bar = '█' * int((count / len(tickets)) * 20) if tickets else ''
            emoji = get_status_emoji(status)
            print(f"  {emoji} {status:15s}: {count:3d} ({pct:5.1f}%) {bar}")
    
    print(f"\n📁 By Category:")
    for category, count in sorted(by_category.items(), key=lambda x: x[1], reverse=True):
        pct = (count / len(tickets)) * 100
        bar = '█' * int(pct / 2)
        print(f"  {category:25s}: {count:3d} ({pct:5.1f}%) {bar}")
    
    print(f"\n🏷️ By Epic:")
    for epic, count in sorted(by_epic.items(), key=lambda x: x[1], reverse=True):
        pct = (count / len(tickets)) * 100
        bar = '█' * int(pct / 2)
        print(f"  {epic[:50]:50s}: {count:3d} ({pct:5.1f}%) {bar}")
    
    print(f"\n📈 By Complexity:")
    for complexity in ['Critical', 'High', 'Medium', 'Low']:
        count = by_complexity.get(complexity, 0)
        pct = (count / len(tickets)) * 100
        bar = '█' * int(pct / 2)
        emoji = get_complexity_emoji(complexity)
        print(f"  {emoji} {complexity:10s}: {count:3d} ({pct:5.1f}%) {bar}")
    
    print("\n" + "=" * 80)


def cmd_by_epic(args: List[str]) -> None:
    """Group tickets by epic."""
    tickets = load_tickets()
    groups = defaultdict(list)
    for ticket in tickets:
        key = ticket.get('Epic', 'Unknown')
        groups[key].append(ticket)
    
    print(f"\nTickets grouped by Epic:")
    print("=" * 80)
    
    for key in sorted(groups.keys()):
        group_tickets = groups[key]
        total_min = sum(t.get('EstimateHoursMin', 0) for t in group_tickets)
        total_max = sum(t.get('EstimateHoursMax', 0) for t in group_tickets)
        
        print(f"\n{key}")
        print(f"  Count: {len(group_tickets)}, Effort: {total_min}-{total_max} hours")
        print("-" * 40)
        for ticket in group_tickets:
            print(f"    {ticket['ID']}: {ticket['Title']} [{ticket['Priority']}]")


def cmd_by_priority(args: List[str]) -> None:
    """Group tickets by priority."""
    tickets = load_tickets()
    groups = defaultdict(list)
    for ticket in tickets:
        key = ticket.get('Priority', 'Unknown')
        groups[key].append(ticket)
    
    for priority in ['P0', 'P1', 'P2', 'P3']:
        if priority in groups:
            tickets_p = groups[priority]
            print(f"\n{priority} ({len(tickets_p)} tickets):")
            for ticket in tickets_p:
                print(f"  {ticket['ID']}: {ticket['Title']} [{ticket['Status']}]")


def cmd_by_category(args: List[str]) -> None:
    """Group tickets by category."""
    tickets = load_tickets()
    groups = defaultdict(list)
    for ticket in tickets:
        key = ticket.get('Category', 'Unknown')
        groups[key].append(ticket)
    
    for category in sorted(groups.keys(), key=lambda k: len(groups[k]), reverse=True):
        group_tickets = groups[category]
        print(f"\n{category} ({len(group_tickets)} tickets):")
        for ticket in group_tickets:
            print(f"  {ticket['ID']}: {ticket['Title']} [{ticket['Priority']}] [{ticket['Status']}]")


def cmd_by_complexity(args: List[str]) -> None:
    """Group tickets by complexity."""
    tickets = load_tickets()
    groups = defaultdict(list)
    for ticket in tickets:
        key = ticket.get('Complexity', 'Unknown')
        groups[key].append(ticket)
    
    for complexity in ['Critical', 'High', 'Medium', 'Low']:
        if complexity in groups:
            tickets_c = groups[complexity]
            emoji = get_complexity_emoji(complexity)
            print(f"\n{emoji} {complexity} ({len(tickets_c)} tickets):")
            for ticket in tickets_c:
                print(f"  {ticket['ID']}: {ticket['Title']} [{ticket['Priority']}] [{ticket['Status']}]")


def cmd_search(args: List[str]) -> None:
    """Search tickets by keyword."""
    if not args:
        print("Usage: search <term>")
        return
    
    term = args[0].lower()
    tickets = load_tickets()
    
    results = []
    for ticket in tickets:
        if (term in ticket['Title'].lower() or 
            term in ticket.get('Description', '').lower() or
            term in ticket.get('Description', '').lower() or
            term in ticket.get('AcceptanceCriteria', '').lower() or
            term in ticket.get('Epic', '').lower() or
            term in ticket.get('Category', '').lower() or
            term in ticket.get('Tags', '').lower()):
            results.append(ticket)
    
    print(f"Found {len(results)} tickets matching '{term}':\n")
    print_tickets(results)


def cmd_export(args: List[str]) -> None:
    """Export tickets in various formats."""
    if not args:
        print("Usage: export <format> [output_file]")
        print("Formats: csv, json, markdown")
        return
    
    fmt = args[0].lower()
    output_file = args[1] if len(args) > 1 else None
    tickets = load_tickets()
    
    if fmt == 'csv':
        output = []
        for t in tickets:
            row = {k: t.get(k, '') for k in ['ID', 'Epic', 'Title', 'Priority', 'Complexity', 'EstimateHoursMin', 'EstimateHoursMax', 'Category', 'File', 'Description', 'AcceptanceCriteria', 'Dependencies', 'Status', 'Priority', 'Tags', 'DependsOn', 'Blocks', 'Owner', 'SeedKey', 'CreatedAt', 'UpdatedAt']}
            output.append(list(row.values()))
        
        import io
        output_io = io.StringIO()
        writer = csv.writer(output_io)
        writer.writerow(['ID', 'Epic', 'Title', 'Priority', 'Complexity', 'EstimateHoursMin', 'EstimateHoursMax', 'Category', 'File', 'Description', 'AcceptanceCriteria', 'Dependencies', 'Status', 'Priority', 'Tags', 'DependsOn', 'Blocks', 'Owner', 'SeedKey', 'CreatedAt', 'UpdatedAt'])
        writer.writerows(output)
        result = output_io.getvalue()
    
    elif fmt == 'json':
        result = json.dumps(tickets, indent=2, ensure_ascii=False)
    
    elif fmt == 'markdown':
        lines = ["# Fluxrecord Tickets\n"]
        for t in tickets:
            lines.append(f"\n## {t['ID']}: {t['Title']}")
            lines.append(f"- **Epic**: {t.get('Epic', 'N/A')}")
            lines.append(f"- **Priority**: {t.get('Priority', 'N/A')}")
            lines.append(f"- **Complexity**: {t.get('Complexity', 'N/A')}")
            lines.append(f"- **Status**: {t.get('Status', 'N/A')}")
            lines.append(f"- **Effort**: {t.get('EstimateHoursMin', '?')}-{t.get('EstimateHoursMax', '?')} hours")
            lines.append(f"- **Category**: {t.get('Category', 'N/A')}")
            if t.get('Description'):
                lines.append(f"- **Description**: {t['Description']}")
            if t.get('AcceptanceCriteria'):
                lines.append(f"- **Acceptance**: {t['AcceptanceCriteria']}")
            lines.append("")
        result = "\n".join(lines)
    
    else:
        print(f"Unknown format: {fmt}")
        return
    
    if output_file:
        with open(output_file, 'w', encoding='utf-8') as f:
            f.write(result)
        print(f"Exported to {output_file}")
    else:
        print(result)


def cmd_sprint(args: List[str]) -> None:
    """Show tickets for a specific sprint."""
    if not args:
        print("Usage: sprint <1-10>")
        print("\nSprint assignments:")
        print("  1: Core Infrastructure (M-397001-M-397010)")
        print("  2: Security & Auth (M-397016-M-397021)")
        print("  3: Observability (M-397022-M-397024)")
        print("  4: Testing (M-397025)")
        print("  5: Developer Experience (M-397026-M-397028)")
        print("  6: UI/UX (M-397028)")
        print("  7: Features (M-397029)")
        print("  8: Data & Analytics (M-397030)")
        print("  9: Infrastructure (M-397031)")
        return
    
    try:
        sprint = int(args[0])
    except ValueError:
        print("Sprint must be a number 1-10")
        return
    
    sprint_ranges = {
        1: (397001, 397010),
        2: (397016, 397021),
        3: (397022, 397024),
        4: (397025, 397025),
        5: (397026, 397028),
        6: (397028, 397028),
        7: (397029, 397029),
        8: (397030, 397030),
        9: (397031, 397031),
        10: (397031, 397031),
    }
    
    if sprint not in sprint_ranges:
        print("Invalid sprint number (1-10)")
        return
    
    start, end = sprint_ranges[sprint]
    tickets = load_tickets()
    sprint_tickets = [t for t in tickets if start <= int(t['ID'][2:]) <= end]
    
    print(f"\nSprint {sprint} ({start}-{end}): {len(sprint_tickets)} tickets")
    print("=" * 80)
    print_tickets(sprint_tickets)


def cmd_tag(args: List[str]) -> None:
    """Show tickets by tag."""
    if not args:
        print("Usage: tag <tag-name>")
        return
    
    tag = args[0].lower()
    tickets = load_tickets()
    
    results = []
    for ticket in tickets:
        tags = ticket.get('Tags', '').lower().split(',')
        if tag in [t.strip() for t in tags]:
            results.append(ticket)
    
    print(f"Found {len(results)} tickets with tag '{tag}':\n")
    print_tickets(results)


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return
    
    command = sys.argv[1]
    args = sys.argv[2:]
    
    commands = {
        'list': cmd_list,
        'show': cmd_show,
        'stats': cmd_stats,
        'by-epic': cmd_by_epic,
        'by-priority': cmd_by_priority,
        'by-category': cmd_by_category,
        'by-complexity': cmd_by_complexity,
        'sprint': cmd_sprint,
        'export': cmd_export,
        'search': cmd_search,
        'tag': cmd_tag,
    }
    
    if command in commands:
        commands[command](sys.argv[2:])
    else:
        print(f"Unknown command: {command}")
        print(__doc__)


if __name__ == '__main__':
    main()